from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import UserContext, get_current_user
from app.database import get_db
from app.services.pvc_bom_service import get_pvc_bom_detail, list_pvc_boms, update_pvc_bom_fees


router = APIRouter()


class PVCBomFeeRequest(BaseModel):
    process_fee: str
    package_fee: str


def _require_reviewer(user: UserContext):
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="仅审价科账号可以查看和维护 PVC 母料 BOM")


@router.get("")
def get_boms(
    keyword: str = "",
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    return {"items": list_pvc_boms(db, keyword)}


@router.get("/{bom_no}")
def get_bom_detail(
    bom_no: str,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    result = get_pvc_bom_detail(db, bom_no)
    if not result:
        raise HTTPException(status_code=404, detail="未找到 PVC 母料 BOM")
    return result


@router.patch("/{bom_no}/fees")
def save_bom_fees(
    bom_no: str,
    req: PVCBomFeeRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    _require_reviewer(user)
    try:
        return {"main": update_pvc_bom_fees(db, bom_no, req.process_fee, req.package_fee, user.display_name)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

