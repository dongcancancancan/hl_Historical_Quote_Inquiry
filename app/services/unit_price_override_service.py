from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.models.quotation import QuotationFieldOverride, QuotationMain



def load_unit_price_overrides(db: Session, quotation_id: int) -> dict[int, Decimal]:
    rows = (
        db.query(QuotationFieldOverride)
        .filter(
            QuotationFieldOverride.quotation_main_id == quotation_id,
            QuotationFieldOverride.entity_type == "material",
            QuotationFieldOverride.field_name == "unit_price",
            QuotationFieldOverride.enabled == True,
        )
        .all()
    )
    return {row.record_id: row.value_numeric for row in rows}


def apply_unit_price_overrides(quotation: QuotationMain, overrides: dict[int, Decimal]):
    for item in quotation.materials:
        if item.deleted:
            continue
        value = overrides.get(item.id)
        if value is not None:
            set_committed_value(item, "unit_price", value)



def has_unit_price_override(item, overrides: dict[int, Decimal]) -> bool:
    return item.id in overrides


def upsert_unit_price_override(
    db: Session,
    quotation: QuotationMain,
    material_id: int,
    value,
    base_value,
    operator: str,
) -> QuotationFieldOverride:
    now = datetime.now()
    row = (
        db.query(QuotationFieldOverride)
        .filter(
            QuotationFieldOverride.quotation_main_id == quotation.id,
            QuotationFieldOverride.entity_type == "material",
            QuotationFieldOverride.record_id == material_id,
            QuotationFieldOverride.field_name == "unit_price",
        )
        .first()
    )
    numeric_value = Decimal(str(value))
    numeric_base = Decimal(str(base_value or 0))
    if row:
        row.value_numeric = numeric_value
        row.base_value_numeric = numeric_base
        row.enabled = True
        row.updater = operator
        row.update_time = now
        return row

    row = QuotationFieldOverride(
        quotation_main_id=quotation.id,
        entity_type="material",
        record_id=material_id,
        field_name="unit_price",
        value_numeric=numeric_value,
        base_value_numeric=numeric_base,
        enabled=True,
        creator=operator,
        updater=operator,
    )
    db.add(row)
    return row


def disable_unit_price_override(
    db: Session,
    quotation_id: int,
    material_id: int,
    operator: str,
) -> bool:
    row = (
        db.query(QuotationFieldOverride)
        .filter(
            QuotationFieldOverride.quotation_main_id == quotation_id,
            QuotationFieldOverride.entity_type == "material",
            QuotationFieldOverride.record_id == material_id,
            QuotationFieldOverride.field_name == "unit_price",
            QuotationFieldOverride.enabled == True,
        )
        .first()
    )
    if not row:
        return False
    row.enabled = False
    row.updater = operator
    row.update_time = datetime.now()
    return True
