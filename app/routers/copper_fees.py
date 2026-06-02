import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import UserContext, get_current_user
from app.database import get_db
from app.models.copper_fee import CopperProcessingFee, CopperProcessingFeeLog
from app.services.copper_fee_service import (
    create_copper_fee,
    disable_copper_fee,
    import_copper_fees,
    list_copper_fees,
    match_copper_processing_fee,
    serialize_fee,
    serialize_log,
    update_copper_fee,
)


router = APIRouter()


class CopperFeeRequest(BaseModel):
    copper_type: str
    diameter: str
    tin_price_basis: str | None = None
    processing_fee: str
    minimum_fee: str | None = None
    remark: str = ""
    enabled: bool = True


def _require_reviewer(user: UserContext):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以维护铜加工费")


@router.get("")
def get_copper_fees(
    copper_type: str = "",
    keyword: str = "",
    include_disabled: bool = False,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    try:
        fees = list_copper_fees(db, copper_type, keyword, include_disabled)
        return {"items": [serialize_fee(fee) for fee in fees]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/match")
def match_copper_fee(
    material_code: str,
    tin_price_basis: str | None = None,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    try:
        parsed, fee = match_copper_processing_fee(db, material_code, tin_price_basis)
        return {
            "matched": fee is not None,
            "material_code": material_code,
            "copper_type": parsed["copper_type"],
            "diameter": str(parsed["diameter"]),
            "tin_price_basis": str(parsed["tin_price_basis"]),
            "fee": serialize_fee(fee) if fee else None,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("")
def add_copper_fee(
    req: CopperFeeRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    try:
        fee = create_copper_fee(db, req.model_dump(), user.display_name)
        return {"item": serialize_fee(fee)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{fee_id}")
def edit_copper_fee(
    fee_id: int,
    req: CopperFeeRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    fee = db.query(CopperProcessingFee).filter(CopperProcessingFee.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="未找到铜加工费记录")
    try:
        fee = update_copper_fee(db, fee, req.model_dump(), user.display_name)
        return {"item": serialize_fee(fee)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{fee_id}")
def remove_copper_fee(
    fee_id: int,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    fee = db.query(CopperProcessingFee).filter(CopperProcessingFee.id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="未找到铜加工费记录")
    disable_copper_fee(db, fee, user.display_name)
    return {"ok": True}


@router.get("/{fee_id}/logs")
def get_copper_fee_logs(
    fee_id: int,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    logs = (
        db.query(CopperProcessingFeeLog)
        .filter(CopperProcessingFeeLog.fee_id == fee_id)
        .order_by(CopperProcessingFeeLog.operate_time.desc(), CopperProcessingFeeLog.id.desc())
        .all()
    )
    return {"items": [serialize_log(log) for log in logs]}


@router.post("/import/excel")
async def import_copper_fee_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 文件")
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as temp_file:
            temp_file.write(await file.read())
            temp_path = temp_file.name
        return import_copper_fees(db, temp_path, user.display_name)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
