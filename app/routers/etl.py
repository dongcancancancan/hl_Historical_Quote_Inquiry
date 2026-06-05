import os
import uuid
import json
import time
import shutil
import logging
from urllib.parse import quote
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.auth import UserContext, get_current_user
from app.models.quotation import QuotationMain
from app.services.etl_service import scan_quotations, process_excel_streaming, get_upload_history, delete_quotation
from app.services.excel_preview_service import (
    clear_material_unit_prices,
    get_accessible_quotation,
    get_review_history,
    get_review_status,
    render_quotation_preview,
    set_review_status,
    update_quotation_fields,
)
from app.services.calc_param_service import get_or_create_calc_params, serialize_calc_params, update_calc_params
from app.services.conductor_calc_service import calculate_conductor_materials, list_conductor_traces
from app.services.copper_scenario_service import calculate_bpm_copper_scenarios
from app.services.glue_calc_service import calculate_glue_materials, list_glue_traces
from app.services.price_summary_calc_service import calculate_price_summary, list_price_summary_traces
from app.services.full_price_calc_service import calculate_full_price
from app.services.calculation_skill_engine import list_calculation_skills
from app.services.calculation_diagnosis_service import diagnose_calculation
from app.services.excel_service import render_quotation_excel
from app.services.bpm_lookup_service import (
    build_quotation_code_filter,
    get_bpm_flows_by_quotation_codes,
    get_quotation_codes_by_bpm,
    normalize_bpm_no,
    resolve_bpm_no,
)

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
    copper_price: str | None = None
    copper_rod_process_fee: str = "1055"
    vat_rate: str = "1.13"


class BatchCalcParamRequest(QuotationCalcParamRequest):
    quotation_codes: list[str]
    calculate_after_save: bool = False


class BatchDeleteRequest(BaseModel):
    quotation_codes: list[str]


class CopperScenarioRequest(BaseModel):
    bpm_no: str


class CalculationDiagnosisRequest(BaseModel):
    error_message: str | None = None


@router.post("/upload_excel")
async def upload_and_process_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
    request: Request = None,
):
    """上传 Excel，扫描后通过 SSE 流式返回处理进度"""
    if user.is_reviewer:
        raise HTTPException(status_code=403, detail="审价科账号不允许上传成本分析表")
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
    query = db.query(QuotationMain).filter(QuotationMain.deleted == False)
    bpm_code = ""
    codes: list[str] = []
    if bpm_no:
        bpm_code = normalize_bpm_no(bpm_no)
        codes = get_quotation_codes_by_bpm(db, bpm_code)
        if not codes:
            return {"items": [], "bpm_no": bpm_code, "mapped_codes": []}
        query = query.filter(build_quotation_code_filter(QuotationMain.quotation_code, codes))
    rows = query.order_by(QuotationMain.create_time.desc()).limit(1000).all()
    bpm_map = get_bpm_flows_by_quotation_codes(
        db,
        [quotation.quotation_code for quotation in rows if quotation.quotation_code],
    )
    items = []
    for quotation in rows:
        review_status = get_review_status(quotation)
        if status and review_status != status:
            continue
        items.append({
            "quotation_code": quotation.quotation_code or "",
            "bpm_no": bpm_code if bpm_no else resolve_bpm_no(bpm_map, quotation.quotation_code, quotation.bpm_no),
            "customer_name": quotation.customer_name or "",
            "product_spec": quotation.product_spec or "",
            "upload_user": quotation.creator or "",
            "create_time": quotation.create_time.isoformat() if quotation.create_time else None,
            "review_status": review_status,
            "final_selling_price": str(quotation.final_selling_price or ""),
        })
    return {"items": items, "bpm_no": bpm_code, "mapped_codes": codes if bpm_no else []}


@router.delete("/quotation")
def remove_quotation(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """删除指定成本分析号（管理员可删任意，普通用户仅可删自己）"""
    ok = delete_quotation(db, code, user.tenant_id, user.display_name, user.is_admin)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限删除")
    return {"ok": True}


@router.get("/quotation/preview", response_class=HTMLResponse)
def preview_quotation(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """按成本分析号查询数据库并返回成本分析表网页预览。"""
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限查看")
    try:
        return HTMLResponse(render_quotation_preview(quotation))
    except Exception as exc:
        logger.exception("Excel preview failed for %s", code)
        raise HTTPException(status_code=500, detail=f"Excel 预览生成失败: {exc}")


@router.get("/quotation/calc-params")
def get_quotation_calc_params(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以维护计算参数")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限查看")
    params = get_or_create_calc_params(db, quotation, user.display_name)
    return serialize_calc_params(params)


@router.patch("/quotation/calc-params")
def save_quotation_calc_params(
    code: str,
    req: QuotationCalcParamRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以维护计算参数")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限维护")
    try:
        params = update_calc_params(db, quotation, req.model_dump(), user.display_name)
        return serialize_calc_params(params)
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
    if not codes:
        raise HTTPException(status_code=400, detail="请选择成本分析号")
    updated = 0
    calculated = 0
    skipped = []
    for code in codes:
        quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
        if not quotation:
            skipped.append({"quotation_code": code, "reason": "未找到或无权限"})
            continue
        try:
            update_calc_params(db, quotation, req.model_dump(), user.display_name)
            updated += 1
            if req.calculate_after_save:
                calculate_conductor_materials(db, quotation, user.display_name)
                calculate_price_summary(db, quotation, user.display_name)
                calculated += 1
        except ValueError as exc:
            db.rollback()
            skipped.append({"quotation_code": code, "reason": str(exc)})
        except Exception as exc:
            db.rollback()
            logger.exception("Batch calc params failed for %s", code)
            skipped.append({"quotation_code": code, "reason": str(exc)})
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
    if not codes:
        raise HTTPException(status_code=400, detail="请选择成本分析号")
    deleted = 0
    skipped = []
    for code in codes:
        try:
            ok = delete_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
            if ok:
                deleted += 1
            else:
                skipped.append({"quotation_code": code, "reason": "未找到、已报价或无权限"})
        except Exception as exc:
            db.rollback()
            skipped.append({"quotation_code": code, "reason": str(exc)})
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
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行导体计算")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限计算")
    try:
        return calculate_conductor_materials(db, quotation, user.display_name)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/calculate/conductor/traces")
def get_quotation_conductor_traces(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看导体计算过程")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限查看")
    return {"items": list_conductor_traces(db, quotation)}


@router.post("/quotation/calculate/glue")
def calculate_quotation_glue(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行胶料计算")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限计算")
    try:
        return calculate_glue_materials(db, quotation, user.display_name)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/calculate/glue/traces")
def get_quotation_glue_traces(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看胶料计算过程")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限查看")
    return {"items": list_glue_traces(db, quotation)}


@router.post("/quotation/calculate/price-summary")
def calculate_quotation_price_summary(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行售价汇总计算")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限计算")
    try:
        return calculate_price_summary(db, quotation, user.display_name)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/quotation/calculate/full-price")
def calculate_quotation_full_price(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行最终售价计算")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限计算")
    try:
        return calculate_full_price(db, quotation, user.display_name)
    except ValueError as exc:
        db.rollback()
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
    code: str,
    req: CalculationDiagnosisRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以执行计算诊断")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限查看")
    return await diagnose_calculation(db, quotation, req.error_message)


@router.get("/quotation/calculate/price-summary/traces")
def get_quotation_price_summary_traces(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看售价汇总计算过程")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限查看")
    return {"items": list_price_summary_traces(db, quotation)}


@router.patch("/quotation")
def update_quotation(
    code: str,
    req: QuotationUpdateRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """批量更新成本分析表字段，不重新调用 LLM。"""
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限修改")
    try:
        new_code = update_quotation_fields(
            db,
            quotation,
            [change.model_dump() for change in req.changes],
            user.display_name,
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
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以清空单价")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限修改")
    try:
        result = clear_material_unit_prices(db, quotation, user.display_name)
        return {"ok": True, **result}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Clear quotation unit prices failed for %s", code)
        raise HTTPException(status_code=500, detail="清空单价失败")


@router.patch("/quotation/review-status")
def update_review_status(
    code: str,
    req: QuotationReviewStatusRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """审价科标记待报价 / 已报价状态。"""
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以修改报价状态")
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, True)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号")
    try:
        status = set_review_status(db, quotation, req.status, user.display_name)
        return {"ok": True, "quotation_code": quotation.quotation_code, "review_status": status}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/quotation/export")
def export_quotation(
    code: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """按成本分析号从数据库生成并下载 Excel。"""
    quotation = get_accessible_quotation(db, code, user.tenant_id, user.display_name, user.is_admin, user.is_reviewer)
    if not quotation:
        raise HTTPException(status_code=404, detail="未找到该成本分析号或无权限导出")
    try:
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
