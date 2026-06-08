from sqlalchemy.orm import Session

from app.models.quotation import QuotationMain
from app.services.calculation_skill_engine import run_full_price_skills


def calculate_full_price(db: Session, quotation: QuotationMain, operator: str) -> dict:
    try:
        return run_full_price_skills(db, quotation, operator)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
