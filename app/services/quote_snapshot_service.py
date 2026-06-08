from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationRun, QuotationQuoteSnapshot
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.services.calculation_run_service import latest_successful_run, mark_run_adopted


def create_quote_snapshot(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance,
    quoted_by: str,
    run: QuotationCalculationRun | None = None,
) -> QuotationQuoteSnapshot:
    run = run or latest_successful_run(db, quotation, instance)
    now = datetime.now()
    db.query(QuotationQuoteSnapshot).filter(
        QuotationQuoteSnapshot.bpm_instance_id == instance.id,
        QuotationQuoteSnapshot.active == True,
        QuotationQuoteSnapshot.deleted == False,
    ).update({"active": False, "deleted": True}, synchronize_session=False)

    mark_run_adopted(db, run)
    snapshot_data = build_snapshot_data(quotation, instance, run)
    snapshot = QuotationQuoteSnapshot(
        quotation_main_id=quotation.id,
        bpm_instance_id=instance.id,
        calculation_run_id=run.id if run else None,
        quotation_code=quotation.quotation_code or "",
        bpm_no=instance.bpm_no or "",
        quote_date=instance.quote_date,
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False, default=_json_default),
        final_selling_price=instance.final_selling_price or quotation.final_selling_price,
        quoted_by=quoted_by,
        quoted_time=now,
        active=True,
        deleted=False,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def get_active_snapshot(
    db: Session,
    instance: QuotationBpmInstance | None,
) -> QuotationQuoteSnapshot | None:
    if not instance:
        return None
    return (
        db.query(QuotationQuoteSnapshot)
        .filter(
            QuotationQuoteSnapshot.bpm_instance_id == instance.id,
            QuotationQuoteSnapshot.active == True,
            QuotationQuoteSnapshot.deleted == False,
        )
        .order_by(QuotationQuoteSnapshot.quoted_time.desc(), QuotationQuoteSnapshot.id.desc())
        .first()
    )


def snapshot_dict(snapshot: QuotationQuoteSnapshot) -> dict:
    try:
        data = json.loads(snapshot.snapshot_data or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_snapshot_data(
    quotation: QuotationMain,
    instance: QuotationBpmInstance,
    run: QuotationCalculationRun | None,
) -> dict:
    materials = sorted(
        [item for item in quotation.materials if not item.deleted],
        key=lambda item: (item.seq_no or 0, item.id or 0),
    )
    processes = sorted(
        [item for item in quotation.processes if not item.deleted],
        key=lambda item: item.id or 0,
    )
    return {
        "meta": {
            "quotation_main_id": quotation.id,
            "bpm_instance_id": instance.id,
            "calculation_run_id": run.id if run else None,
            "bpm_no": instance.bpm_no or "",
            "quote_date": instance.quote_date.isoformat() if instance.quote_date else None,
            "quoted_status": instance.review_status or "",
        },
        "main": {
            "id": quotation.id,
            "quotation_code": quotation.quotation_code or "",
            "customer_name": quotation.customer_name or "",
            "customer_address": quotation.customer_address or "",
            "package_method": getattr(quotation, "package_method", "") or "",
            "analysis_date": instance.quote_date or quotation.analysis_date,
            "structure": quotation.structure or "",
            "braiding_rate": quotation.braiding_rate,
            "product_spec": quotation.product_spec or "",
            "unit_usage_sum": quotation.unit_usage_sum,
            "material_amount_sum": quotation.material_amount_sum,
            "material_cost": quotation.material_cost,
            "ul_label_fee": quotation.ul_label_fee,
            "transport_fee": quotation.transport_fee,
            "packing_fee": quotation.packing_fee,
            "waste_loss_rate": quotation.waste_loss_rate,
            "order_startup_times": quotation.order_startup_times,
            "total_fee": quotation.total_fee,
            "other_fee": quotation.other_fee,
            "irradiation_core_count": quotation.irradiation_core_count,
            "irradiation_core_fee": quotation.irradiation_core_fee,
            "net_profit_rate": quotation.net_profit_rate,
            "customs_fee": quotation.customs_fee,
            "vat_rate": quotation.vat_rate,
            "order_meterage": quotation.order_meterage,
            "operating_expense_rate": quotation.operating_expense_rate,
            "monthly_interest": quotation.monthly_interest,
            "corporate_tax_rate": quotation.corporate_tax_rate,
            "cost": instance.cost if instance.cost is not None else quotation.cost,
            "profit_selling_price": (
                instance.profit_selling_price
                if instance.profit_selling_price is not None
                else quotation.profit_selling_price
            ),
            "non_profit_price": (
                instance.non_profit_price
                if instance.non_profit_price is not None
                else quotation.non_profit_price
            ),
            "final_selling_price": (
                instance.final_selling_price
                if instance.final_selling_price is not None
                else quotation.final_selling_price
            ),
            "remark": quotation.remark or "",
        },
        "materials": [
            {
                "id": item.id,
                "seq_no": item.seq_no,
                "process_name": item.process_name or "",
                "spec_detail": item.spec_detail or "",
                "process_code": item.process_code or "",
                "unit_usage": item.unit_usage,
                "unit_price": item.unit_price,
                "material_amount": item.material_amount,
            }
            for item in materials
        ],
        "processes": [
            {
                "id": item.id,
                "process_name": item.process_name or "",
                "std_hours": item.std_hours,
                "loss_hours": item.loss_hours,
                "fixed_rate": item.fixed_rate,
                "fixed_fee": item.fixed_fee,
                "startup_loss_wire": item.startup_loss_wire,
                "total_waste_glue": item.total_waste_glue,
                "amount": item.amount,
                "subtotal_fee": item.subtotal_fee,
            }
            for item in processes
        ],
    }


def _json_default(value):
    if isinstance(value, Decimal):
        return f"{value:f}".rstrip("0").rstrip(".") or "0"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
