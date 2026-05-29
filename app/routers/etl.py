import os
import uuid
import json
import time
import shutil
import logging
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.auth import UserContext, get_current_user
from app.services.etl_service import scan_quotations, process_excel_streaming

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = "data/original_excels"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload_excel")
async def upload_and_process_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
    request: Request = None,
):
    """上传 Excel，扫描后通过 SSE 流式返回处理进度"""
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
