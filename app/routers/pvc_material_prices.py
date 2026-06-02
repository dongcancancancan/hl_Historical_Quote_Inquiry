from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import UserContext, get_current_user
from app.database import get_db
from app.models.pvc_material_price import PVCMaterialPrice
from app.services.pvc_material_price_service import (
    create_material_price,
    delete_material_price,
    list_material_price_logs,
    list_material_prices,
    serialize_material_price,
    update_material_price,
)


router = APIRouter()


class PVCMaterialPriceRequest(BaseModel):
    prd_no: str
    name: str
    unit: str
    unit_price: str
    effective_date: str | None = None
    remark: str = ""


def _require_reviewer(user: UserContext):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以维护 PVC 材料价格")


@router.get("")
def get_material_prices(
    keyword: str = "",
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    return {"items": list_material_prices(db, keyword)}


@router.get("/logs")
def get_material_price_logs(
    prd_no: str = "",
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    return {"items": list_material_price_logs(db, prd_no)}


@router.post("")
def add_material_price(
    req: PVCMaterialPriceRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    try:
        row = create_material_price(db, req.model_dump(), user.display_name)
        return {"item": serialize_material_price(row)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{row_id}")
def edit_material_price(
    row_id: int,
    req: PVCMaterialPriceRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    row = db.query(PVCMaterialPrice).filter(PVCMaterialPrice.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到 PVC 材料价格记录")
    try:
        row = update_material_price(db, row, req.model_dump(), user.display_name)
        return {"item": serialize_material_price(row)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{row_id}")
def remove_material_price(
    row_id: int,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    row = db.query(PVCMaterialPrice).filter(PVCMaterialPrice.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到 PVC 材料价格记录")
    delete_material_price(db, row, user.display_name)
    return {"ok": True}

