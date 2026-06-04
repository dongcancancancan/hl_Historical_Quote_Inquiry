from sqlalchemy.orm import Session

from app.models.quotation import QuotationMain
from app.services.conductor_calc_service import calculate_conductor_materials
from app.services.glue_calc_service import calculate_glue_materials
from app.services.price_summary_calc_service import calculate_price_summary


def calculate_full_price(db: Session, quotation: QuotationMain, operator: str) -> dict:
    result = {
        "conductor": None,
        "glue": None,
        "price_summary": None,
    }
    try:
        result["conductor"] = calculate_conductor_materials(db, quotation, operator)
    except ValueError as exc:
        raise ValueError(f"导体/编织计算失败：{exc}") from exc

    try:
        result["glue"] = calculate_glue_materials(db, quotation, operator)
    except ValueError as exc:
        raise ValueError(f"胶料/外购及制程费用计算失败：{exc}") from exc

    try:
        result["price_summary"] = calculate_price_summary(db, quotation, operator)
    except ValueError as exc:
        raise ValueError(f"最终售价计算失败：{exc}") from exc

    return result
