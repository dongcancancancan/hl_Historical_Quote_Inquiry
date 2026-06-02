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
from app.services.etl_service import scan_quotations, process_excel_streaming, get_upload_history, delete_quotation
from app.services.excel_preview_service import (
    get_accessible_quotation,
    get_review_history,
    get_review_status,
    render_quotation_preview,
    set_review_status,
    update_quotation_fields,
)
from app.services.excel_service import render_quotation_excel

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
):
    """返回报价单明细（按日期分组）。管理员可查看所有，普通用户仅看自己的。"""
    history = get_upload_history(db, user.tenant_id, user.display_name, user.is_admin)
    return {"history": history}


@router.get("/review/history")
def review_history(
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """审价科工作台：返回全库待报价和已报价列表。"""
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看")
    return get_review_history(db)


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
