from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.calc_param import QuotationCalcParam
from app.models.quotation import QuotationMain


DEFAULT_COPPER_ROD_PROCESS_FEE = Decimal("1055")
DEFAULT_VAT_RATE = Decimal("1.13")


def get_or_create_calc_params(db: Session, quotation: QuotationMain, operator: str = "SYSTEM") -> QuotationCalcParam:
    params = (
        db.query(QuotationCalcParam)
        .filter(QuotationCalcParam.quotation_main_id == quotation.id)
        .first()
    )
    if params:
        return params
    params = QuotationCalcParam(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        copper_rod_process_fee=DEFAULT_COPPER_ROD_PROCESS_FEE,
        vat_rate=DEFAULT_VAT_RATE,
        creator=operator,
        updater=operator,
        update_time=datetime.now(),
    )
    db.add(params)
    db.commit()
    return params


def update_calc_params(db: Session, quotation: QuotationMain, data: dict, operator: str) -> QuotationCalcParam:
    params = get_or_create_calc_params(db, quotation, operator)
    params.quotation_code = quotation.quotation_code or params.quotation_code
    params.copper_price = _optional_positive_decimal(data.get("copper_price"), "铜价")
    params.copper_rod_process_fee = _required_decimal(
        data.get("copper_rod_process_fee", DEFAULT_COPPER_ROD_PROCESS_FEE),
        "铜杆加工费",
    )
    params.vat_rate = _required_positive_decimal(data.get("vat_rate", DEFAULT_VAT_RATE), "增值税率")
    params.updater = operator
    params.update_time = datetime.now()
    db.commit()
    return params


def serialize_calc_params(params: QuotationCalcParam) -> dict:
    return {
        "quotation_code": params.quotation_code or "",
        "copper_price": _decimal_text(params.copper_price),
        "copper_rod_process_fee": _decimal_text(params.copper_rod_process_fee),
        "vat_rate": _decimal_text(params.vat_rate),
        "updater": params.updater or "",
        "update_time": params.update_time.isoformat() if params.update_time else None,
    }


def _optional_decimal(value, label: str):
    if value in (None, ""):
        return None
    return _required_decimal(value, label)


def _optional_positive_decimal(value, label: str):
    if value in (None, ""):
        return None
    return _required_positive_decimal(value, label)


def _required_positive_decimal(value, label: str) -> Decimal:
    result = _required_decimal(value, label)
    if result <= 0:
        raise ValueError(f"{label}必须大于 0")
    return result


def _required_decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if result < 0:
        raise ValueError(f"{label}不能小于 0")
    return result


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
