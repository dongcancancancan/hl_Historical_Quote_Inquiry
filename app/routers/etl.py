import os
import uuid
import json
import time
import shutil
import logging
from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.auth import UserContext, get_current_user
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.services.etl_service import scan_quotations, process_excel_streaming, get_upload_history, delete_quotation
from app.services.excel_preview_service import (
    clear_material_unit_prices,
    get_review_history,
    get_review_status,
    render_quote_snapshot_preview,
    render_quotation_preview,
    set_review_status,
    update_quotation_fields,
)
from app.services.bpm_instance_service import (
    get_accessible_quotation_context,
    serialize_instance_calc_params,
    snapshot_instance_from_quotation,
    sync_instance_calc_params_to_engine,
    update_instance_calc_params,
)
from app.services.conductor_calc_service import calculate_conductor_materials, list_conductor_traces
from app.services.copper_scenario_service import calculate_bpm_copper_scenarios
from app.services.glue_calc_service import calculate_glue_materials, list_glue_traces
from app.services.price_summary_calc_service import calculate_price_summary, list_price_summary_traces
from app.services.full_price_calc_service import calculate_full_price
from app.services.calculation_skill_engine import list_calculation_skills
from app.services.calculation_diagnosis_service import diagnose_calculation
from app.services.excel_service import render_quote_snapshot_excel, render_quotation_excel
from app.services.calculation_run_service import latest_successful_run, record_successful_calculation_run
from app.services.quote_snapshot_service import create_quote_snapshot, get_active_snapshot, snapshot_dict

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = "data/original_excels"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB
os.makedirs(UPLOAD_DIR, exist_ok=True)


class QuotationFieldChange(BaseModel):
    entity: str
    id: int
    field: str
    value: str | None = None


class QuotationUpdateRequest(BaseModel):
    changes: list[QuotationFieldChange]


class QuotationReviewStatusRequest(BaseModel):
    status: str


class QuotationCalcParamRequest(BaseModel):
    copper_price: str | int | float | None = None
    copper_rod_process_fee: str | int | float = "1055"
    vat_rate: str | int | float = "1.13"
    transport_fee: str | int | float | None = None
    other_fee: str | int | float | None = None
    net_profit_rate: str | int | float | None = None
    customs_fee: str | int | float | None = None
    order_meterage: str | int | float | None = None
    operating_expense_rate: str | int | float | None = None
    monthly_interest: str | int | float | None = None
    corporate_tax_rate: str | int | float | None = None


class BatchCalcParamRequest(QuotationCalcParamRequest):
    quotation_codes: list[str] = Field(default_factory=list)
    instance_ids: list[int] | None = None
    calculate_after_save: bool = False


class BatchDeleteRequest(BaseModel):
    quotation_codes: list[str] = Field(default_factory=list)
    instance_ids: list[int] | None = None


class CopperScenarioRequest(BaseModel):
    bpm_no: str


class CalculationDiagnosisRequest(BaseModel):
    error_message: str | None = None


def _get_context_or_404(
    db: Session,
    user: UserContext,
    code: str | None = None,
    instance_id: int | None = None,
):
    context = get_accessible_quotation_context(
        db,
        code,
        instance_id,
        user.tenant_id,
        user.display_name,
        user.is_admin,
        user.is_reviewer,
    )
    if not context:
        raise HTTPException(status_code=404, detail="未找到该成本分析表或无权限查看")
    return context


def _prepare_instance_calculation(quotation: QuotationMain, instance: QuotationBpmInstance | None) -> str | None:
    if get_review_status(quotation, instance) == "quoted":
        raise ValueError("该 BPM 实例已报价，只能查看，不能重新计算")
    old_tags = quotation.extracted_tags
    if not instance:
        return old_tags
    try:
        tags = json.loads(old_tags or "{}")
        tags = tags if isinstance(tags, dict) else {}
    except Exception:
        tags = {}
    if tags.get("review_status") == "quoted":
        tags["review_status"] = "pending"
        quotation.extracted_tags = json.dumps(tags, ensure_ascii=False)
    return old_tags


def _restore_instance_calculation(quotation: QuotationMain, instance: QuotationBpmInstance | None, old_tags: str | None) -> None:
    if instance:
        quotation.extracted_tags = old_tags


def _selected_trace_run_id(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    calc_type: str,
    run_id: int | None,
) -> int | None:
    if run_id:
        return run_id
    run = latest_successful_run(db, quotation, instance, calc_type)
    if not run and instance:
        run = latest_successful_run(db, quotation, None, calc_type)
    return run.id if run else None


def _clear_full_price_outputs(quotation: QuotationMain, instance: QuotationBpmInstance | None, operator: str) -> None:
    now = datetime.now()
    quotation.unit_usage_sum = None
    quotation.material_amount_sum = None
    quotation.material_cost = None
    quotation.total_fee = None
    quotation.cost = None
    quotation.profit_selling_price = None
    quotation.non_profit_price = None
    quotation.final_selling_price = None
    quotation.updater = operator
    quotation.update_time = now
    if instance:
        instance.cost = None
        instance.profit_selling_price = None
        instance.non_profit_price = None
        instance.final_selling_price = None
        instance.updater = operator
        instance.update_time = now


@router.post("/upload_excel")
async def upload_and_process_excel(
    file: UploadFile = File(...),
    bpm_no: str = Form(""),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
    request: Request = None,
):
    """上传 Excel，扫描后通过 SSE 流式返回处理进度"""
    if user.is_reviewer:
        raise HTTPException(status_code=403, detail="审价科账号不允许上传成本分析表")
    bpm_no = (bpm_no or "").strip().upper()
    if not bpm_no:
        raise HTTPException(status_code=400, detail="请先填写 BPM流程号")
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 或 .xls 格式的文件")

    # 保存文件（逐块读取以控制大小）
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    saved_path = os.path.join(UPLOAD_DIR, unique_filename)

    total_bytes = 0
    with open(saved_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                buffer.close()
                os.remove(saved_path)
                raise HTTPException(status_code=413, detail="文件大小超过限制，最大支持 50MB")
            buffer.write(chunk)

    logger.info(f"Saved original excel to {saved_path} ({total_bytes / 1024 / 1024:.1f}MB)")

    # 快速扫描（无需 LLM）
    t_scan = time.time()
    try:
        blocks = scan_quotations(saved_path)
    except Exception as e:
        logger.error(f"Excel scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"Excel 解析失败: {str(e)}")

    total = len(blocks)
    scan_time = round(time.time() - t_scan, 2)
    logger.info(f"扫描完成: {total} 个报价单, 耗时 {scan_time}s")

    async def event_stream():
        # 先发扫描结果
        yield f"data: {json.dumps({'event': 'scanned', 'total': total, 'scan_time': scan_time, 'filename': file.filename}, ensure_ascii=False)}\n\n"

        if total == 0:
            yield f"data: {json.dumps({'event': 'complete', 'processed': 0, 'errors': 0}, ensure_ascii=False)}\n\n"
            return

        gen = process_excel_streaming(
            blocks=blocks,
            saved_path=saved_path,
            db=db,
            tenant_id=user.tenant_id,
            username=user.username,
            display_name=user.display_name,
            bpm_no=bpm_no,
        )
        try:
            async for event in gen:
                # 检查客户端是否断开
                if request and await request.is_disconnected():
                    logger.warning("Client disconnected, stopping ETL stream")
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await gen.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
def upload_history(
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
    search: str = "",
):
    """返回报价单明细（按日期分组）。管理员可查看所有，普通用户仅看自己的。
    支持 search 参数：按成本分析号模糊搜索或按 BPM 流程号精确搜索。"""
    history = get_upload_history(db, user.tenant_id, user.display_name, user.is_admin, search=search)
    return {"history": history}


@router.get("/review/history")
def review_history(
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
    search: str = "",
):
    """审价科工作台：返回全库待报价和已报价列表。"""
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看")
    return get_review_history(db, search=search)


@router.get("/quotations")
def list_quotations(
    bpm_no: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer and not user.is_admin:
        raise HTTPException(status_code=403, detail="无权查看批量报价列表")
    query = (
        db.query(QuotationMain, QuotationBpmInstance)
        .join(QuotationBpmInstance, QuotationBpmInstance.quotation_main_id == QuotationMain.id)
        .filter(QuotationMain.deleted == False, QuotationBpmInstance.deleted == False)
    )
    bpm_code = ""
    if bpm_no:
        bpm_code = bpm_no.strip().upper()
        query = query.filter(QuotationBpmInstance.bpm_no == bpm_code)
    rows = query.order_by(QuotationBpmInstance.upload_time.desc(), QuotationBpmInstance.id.desc()).limit(1000).all()
    items = []
    for quotation, instance in rows:
        review_status = get_review_status(quotation, instance)
        if status and review_status != status:
            continue
        items.append({
            "instance_id": instance.id,
            "quotation_code": quotation.quotation_code or "",
            "bpm_no": instance.bpm_no or quotation.bpm_no or "",
            "customer_name": quotation.customer_name or "",
            "package_method": getattr(quotation, "package_method", "") or "",
            "product_spec": quotation.product_spec or "",
            "upload_user": instance.upload_user or quotation.creator or "",
            "create_time": instance.upload_time.isoformat() if instance.upload_time else None,
            "quote_date": instance.quote_date.isoformat() if instance.quote_date else None,
            "review_status": review_status,
            "final_selling_price": str(instance.final_selling_price or quotation.final_selling_price or ""),
        })
    return {"items": items, "bpm_no": bpm_code, "mapped_codes": [item["quotation_code"] for item in items] if bpm_no else []}


@router.delete("/quotation")
def remove_quotation(
    code: str,
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """删除指定成本分析号（管理员可删任意，普通用户仅可删自己）"""
    try:
        ok = delete_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, instance_id=instance_id)
        if not ok:
            raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限删除")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Delete quotation failed for %s instance_id=%s", code, instance_id)
        raise HTTPException(status_code=500, detail=f"删除失败：{exc}")


@router.get("/quotation/preview", response_class=HTMLResponse)
def preview_quotation(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """按成本分析号查询数据库并返回成本分析表网页预览。"""
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        snapshot = get_active_snapshot(db, instance) if instance and instance.review_status == "quoted" else None
        if snapshot:
            return HTMLResponse(render_quote_snapshot_preview(snapshot_dict(snapshot)))
        return HTMLResponse(render_quotation_preview(quotation, instance))
    except Exception as exc:
        logger.exception("Excel preview failed for %s", code)
        raise HTTPException(status_code=500, detail=f"Excel 预览生成失败: {exc}")


@router.get("/quotation/calc-params")
def get_quotation_calc_params(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以维护计算参数")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    return serialize_instance_calc_params(db, quotation, instance, user.display_name)


@router.patch("/quotation/calc-params")
def save_quotation_calc_params(
    req: QuotationCalcParamRequest,
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以维护计算参数")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        return update_instance_calc_params(db, quotation, instance, req.model_dump(exclude_unset=True), user.display_name)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/quotation/calc-params/batch")
def save_batch_calc_params(
    req: BatchCalcParamRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以批量维护计算参数")
    codes = [code.strip() for code in req.quotation_codes if code and code.strip()]
    instance_ids = [int(item) for item in (req.instance_ids or []) if item]
    if not codes and not instance_ids:
        raise HTTPException(status_code=400, detail="请选择成本分析号")
    updated = 0
    calculated = 0
    skipped = []
    targets = [("instance", item) for item in instance_ids] + [("code", code) for code in codes]
    for target_type, target_value in targets:
        context = get_accessible_quotation_context(
            db,
            "" if target_type == "instance" else str(target_value),
            int(target_value) if target_type == "instance" else None,
            user.tenant_id,
            user.display_name,
            user.is_admin,
            user.is_reviewer,
        )
        if not context:
            skipped.append({"quotation_code": str(target_value), "reason": "未找到或无权限"})
            continue
        quotation, instance = context
        try:
            update_instance_calc_params(db, quotation, instance, req.model_dump(exclude_unset=True), user.display_name)
            updated += 1
            if req.calculate_after_save:
                sync_instance_calc_params_to_engine(db, quotation, instance, user.display_name)
                old_tags = _prepare_instance_calculation(quotation, instance)
                _clear_full_price_outputs(quotation, instance, user.display_name)
                price_result = calculate_full_price(db, quotation, user.display_name)
                _restore_instance_calculation(quotation, instance, old_tags)
                record_successful_calculation_run(db, quotation, instance, "full_price", user.display_name, price_result)
                snapshot_instance_from_quotation(instance, quotation, user.display_name)
                db.commit()
                calculated += 1
        except ValueError as exc:
            db.rollback()
            skipped.append({"quotation_code": quotation.quotation_code or str(target_value), "reason": str(exc)})
        except Exception as exc:
            db.rollback()
            logger.exception("Batch calc params failed for %s", target_value)
            skipped.append({"quotation_code": quotation.quotation_code or str(target_value), "reason": str(exc)})
    return {"updated": updated, "calculated": calculated, "skipped": skipped}


@router.delete("/quotations/batch")
def remove_quotations_batch(
    req: BatchDeleteRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer and not user.is_admin:
        raise HTTPException(status_code=403, detail="无权批量删除")
    codes = [code.strip() for code in req.quotation_codes if code and code.strip()]
    instance_ids = [int(item) for item in (req.instance_ids or []) if item]
    if not codes and not instance_ids:
        raise HTTPException(status_code=400, detail="请选择成本分析号")
    deleted = 0
    skipped = []
    targets = [("instance", item) for item in instance_ids] + [("code", code) for code in codes]
    for target_type, target_value in targets:
        code = "" if target_type == "instance" else str(target_value)
        try:
            ok = delete_quotation(
                db,
                code,
                user.tenant_id,
                user.display_name,
                user.is_admin,
                user.is_reviewer,
                instance_id=int(target_value) if target_type == "instance" else None,
            )
            if ok:
                deleted += 1
            else:
                skipped.append({"quotation_code": str(target_value), "reason": "未找到、已报价或无权限"})
        except Exception as exc:
            db.rollback()
            skipped.append({"quotation_code": str(target_value), "reason": str(exc)})
    return {"deleted": deleted, "skipped": skipped}


@router.post("/quotation/copper-scenarios")
def calculate_copper_scenarios(
    req: CopperScenarioRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行铜段测算")
    try:
        return calculate_bpm_copper_scenarios(db, req.bpm_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/quotation/calculate/conductor")
def calculate_quotation_conductor(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行导体计算")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        sync_instance_calc_params_to_engine(db, quotation, instance, user.display_name)
        old_tags = _prepare_instance_calculation(quotation, instance)
        result = calculate_conductor_materials(db, quotation, user.display_name)
        _restore_instance_calculation(quotation, instance, old_tags)
        run = record_successful_calculation_run(db, quotation, instance, "conductor", user.display_name, result)
        snapshot_instance_from_quotation(instance, quotation, user.display_name)
        db.commit()
        result["calculation_run_id"] = run.id
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/calculate/conductor/traces")
def get_quotation_conductor_traces(
    code: str = "",
    instance_id: int | None = None,
    run_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看导体计算过程")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    selected_run_id = _selected_trace_run_id(db, quotation, instance, "conductor", run_id)
    return {"items": list_conductor_traces(db, quotation, instance.id if instance else None, selected_run_id)}


@router.post("/quotation/calculate/glue")
def calculate_quotation_glue(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行胶料计算")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        sync_instance_calc_params_to_engine(db, quotation, instance, user.display_name)
        old_tags = _prepare_instance_calculation(quotation, instance)
        result = calculate_glue_materials(db, quotation, user.display_name)
        _restore_instance_calculation(quotation, instance, old_tags)
        run = record_successful_calculation_run(db, quotation, instance, "glue", user.display_name, result)
        snapshot_instance_from_quotation(instance, quotation, user.display_name)
        db.commit()
        result["calculation_run_id"] = run.id
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/calculate/glue/traces")
def get_quotation_glue_traces(
    code: str = "",
    instance_id: int | None = None,
    run_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看胶料计算过程")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    selected_run_id = _selected_trace_run_id(db, quotation, instance, "glue", run_id)
    return {"items": list_glue_traces(db, quotation, instance.id if instance else None, selected_run_id)}


@router.post("/quotation/calculate/price-summary")
def calculate_quotation_price_summary(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行售价汇总计算")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        sync_instance_calc_params_to_engine(db, quotation, instance, user.display_name)
        old_tags = _prepare_instance_calculation(quotation, instance)
        result = calculate_price_summary(db, quotation, user.display_name)
        _restore_instance_calculation(quotation, instance, old_tags)
        run = record_successful_calculation_run(db, quotation, instance, "price_summary", user.display_name, result)
        snapshot_instance_from_quotation(instance, quotation, user.display_name)
        db.commit()
        result["calculation_run_id"] = run.id
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/quotation/calculate/full-price")
def calculate_quotation_full_price(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行最终售价计算")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    old_tags = None
    try:
        sync_instance_calc_params_to_engine(db, quotation, instance, user.display_name)
        old_tags = _prepare_instance_calculation(quotation, instance)
        _clear_full_price_outputs(quotation, instance, user.display_name)
        result = calculate_full_price(db, quotation, user.display_name)
        _restore_instance_calculation(quotation, instance, old_tags)
        run = record_successful_calculation_run(db, quotation, instance, "full_price", user.display_name, result)
        snapshot_instance_from_quotation(instance, quotation, user.display_name)
        db.commit()
        result["calculation_run_id"] = run.id
        return result
    except ValueError as exc:
        _restore_instance_calculation(quotation, instance, old_tags)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/calculate/skills")
def get_calculation_skills(
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看计算 Skill")
    return {"items": list_calculation_skills()}


@router.post("/quotation/calculate/diagnose")
async def diagnose_quotation_calculation(
    req: CalculationDiagnosisRequest,
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行计算诊断")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    return await diagnose_calculation(db, quotation, req.error_message)


@router.get("/quotation/calculate/price-summary/traces")
def get_quotation_price_summary_traces(
    code: str = "",
    instance_id: int | None = None,
    run_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看售价汇总计算过程")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    selected_run_id = _selected_trace_run_id(db, quotation, instance, "price_summary", run_id)
    return {"items": list_price_summary_traces(db, quotation, instance.id if instance else None, selected_run_id)}


@router.patch("/quotation")
def update_quotation(
    req: QuotationUpdateRequest,
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """批量更新成本分析表字段，不重新调用 LLM。"""
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        new_code = update_quotation_fields(
            db,
            quotation,
            [change.model_dump() for change in req.changes],
            user.display_name,
            instance,
        )
        return {"ok": True, "quotation_code": new_code, "updated_fields": len(req.changes)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Quotation update failed for %s", code)
        raise HTTPException(status_code=500, detail="成本分析表更新失败")


@router.patch("/quotation/unit-prices/clear")
def clear_quotation_unit_prices(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以清空计算结果")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        result = clear_material_unit_prices(db, quotation, user.display_name, instance)
        return {"ok": True, **result}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Clear quotation calculation results failed for %s", code)
        raise HTTPException(status_code=500, detail="清空计算结果失败")


@router.patch("/quotation/review-status")
def update_review_status(
    req: QuotationReviewStatusRequest,
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """审价科标记待报价 / 已报价状态。"""
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以修改报价状态")
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        status = set_review_status(db, quotation, req.status, user.display_name, instance)
        snapshot_id = None
        calculation_run_id = None
        if status == "quoted" and instance:
            snapshot = create_quote_snapshot(db, quotation, instance, user.display_name)
            db.commit()
            snapshot_id = snapshot.id
            calculation_run_id = snapshot.calculation_run_id
        return {
            "ok": True,
            "quotation_code": quotation.quotation_code,
            "instance_id": instance.id if instance else None,
            "review_status": status,
            "snapshot_id": snapshot_id,
            "calculation_run_id": calculation_run_id,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/export")
def export_quotation(
    code: str = "",
    instance_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """按成本分析号从数据库生成并下载 Excel。"""
    quotation, instance = _get_context_or_404(db, user, code, instance_id)
    try:
        snapshot = get_active_snapshot(db, instance) if instance and instance.review_status == "quoted" else None
        if snapshot:
            buffer = render_quote_snapshot_excel(snapshot_dict(snapshot))
        else:
            buffer = render_quotation_excel(quotation)
        encoded_filename = quote(f"{quotation.quotation_code}.xlsx")
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
        )
    except Exception:
        logger.exception("Quotation export failed for %s", code)
        raise HTTPException(status_code=500, detail="成本分析表导出失败")
