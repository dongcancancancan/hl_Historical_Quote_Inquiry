from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationRun, QuotationCalculationTrace
from app.models.quotation import QuotationBpmInstance, QuotationMain


RUN_TYPE_CALC_TYPES = {
    "conductor": ["conductor"],
    "glue": [
        "glue",
        "external_material",
        "manual_material",
        "insulation",
        "jacket",
        "package_tape",
        "rewind",
        "collection",
    ],
    "price_summary": ["price_summary"],
    "full_price": [
        "conductor",
        "glue",
        "external_material",
        "manual_material",
        "insulation",
        "jacket",
        "package_tape",
        "rewind",
        "collection",
        "price_summary",
    ],
}

RUN_TYPES_BY_CALC_TYPE = {
    "conductor": ["conductor", "full_price"],
    "glue": ["glue", "full_price"],
    "external_material": ["glue", "full_price"],
    "manual_material": ["glue", "full_price"],
    "insulation": ["glue", "full_price"],
    "jacket": ["glue", "full_price"],
    "package_tape": ["glue", "full_price"],
    "rewind": ["glue", "full_price"],
    "collection": ["glue", "full_price"],
    "price_summary": ["price_summary", "full_price"],
}

SKILL_BY_CALC_TYPE = {
    "conductor": "conductor_material_and_process",
    "glue": "glue_external_and_process",
    "external_material": "glue_external_and_process",
    "manual_material": "glue_external_and_process",
    "insulation": "glue_external_and_process",
    "jacket": "glue_external_and_process",
    "package_tape": "glue_external_and_process",
    "rewind": "glue_external_and_process",
    "collection": "glue_external_and_process",
    "price_summary": "price_summary",
}

DISPLAY_LABELS = {
    "unit_price": "单价",
    "material_amount": "材料金额",
    "process_amount": "金额",
    "process_subtotal_fee": "费用成本小计",
    "unit_usage_sum": "单位用量合计",
    "material_amount_sum": "材料金额合计",
    "material_cost": "材料成本",
    "total_fee": "费用总计",
    "cost": "成本",
    "profit_selling_price": "取利售价",
    "non_profit_price": "不取利售价",
    "final_selling_price": "最终售价",
}


def record_successful_calculation_run(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    run_type: str,
    operator: str,
    result_summary: dict,
) -> QuotationCalculationRun:
    now = datetime.now()
    remove_unadopted_runs(db, quotation, instance, [run_type])
    run = QuotationCalculationRun(
        quotation_main_id=quotation.id,
        bpm_instance_id=instance.id if instance else None,
        quotation_code=quotation.quotation_code or "",
        bpm_no=instance.bpm_no if instance else (quotation.bpm_no or ""),
        run_type=run_type,
        status="success",
        params_snapshot=json.dumps(_params_snapshot(instance), ensure_ascii=False),
        result_summary=json.dumps(result_summary or {}, ensure_ascii=False, default=str),
        skill_version="v1",
        operator=operator,
        start_time=now,
        finish_time=now,
    )
    db.add(run)
    db.flush()
    bind_latest_traces_to_run(db, quotation, instance, run, RUN_TYPE_CALC_TYPES.get(run_type, [run_type]))
    db.flush()
    return run


def latest_successful_run(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    calc_type: str | None = None,
) -> QuotationCalculationRun | None:
    query = db.query(QuotationCalculationRun).filter(
        QuotationCalculationRun.quotation_main_id == quotation.id,
        QuotationCalculationRun.status == "success",
    )
    if instance:
        query = query.filter(QuotationCalculationRun.bpm_instance_id == instance.id)
    else:
        query = query.filter(QuotationCalculationRun.bpm_instance_id.is_(None))
    if calc_type:
        query = query.filter(QuotationCalculationRun.run_type.in_(RUN_TYPES_BY_CALC_TYPE.get(calc_type, [calc_type])))
    return query.order_by(QuotationCalculationRun.finish_time.desc(), QuotationCalculationRun.id.desc()).first()


def remove_unadopted_runs(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    run_types: list[str] | None = None,
) -> int:
    query = db.query(QuotationCalculationRun).filter(
        QuotationCalculationRun.quotation_main_id == quotation.id,
        QuotationCalculationRun.status == "success",
        QuotationCalculationRun.is_adopted == False,
    )
    if instance:
        query = query.filter(QuotationCalculationRun.bpm_instance_id == instance.id)
    else:
        query = query.filter(QuotationCalculationRun.bpm_instance_id.is_(None))
    if run_types:
        query = query.filter(QuotationCalculationRun.run_type.in_(run_types))

    run_ids = [row.id for row in query.all()]
    if not run_ids:
        return 0
    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.run_id.in_(run_ids),
    ).delete(synchronize_session=False)
    db.query(QuotationCalculationRun).filter(
        QuotationCalculationRun.id.in_(run_ids),
    ).delete(synchronize_session=False)
    return len(run_ids)


def mark_run_adopted(db: Session, run: QuotationCalculationRun | None) -> None:
    if not run:
        return
    db.query(QuotationCalculationRun).filter(
        QuotationCalculationRun.bpm_instance_id == run.bpm_instance_id,
        QuotationCalculationRun.is_adopted == True,
    ).update({"is_adopted": False}, synchronize_session=False)
    run.is_adopted = True


def bind_latest_traces_to_run(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    run: QuotationCalculationRun,
    calc_types: list[str],
) -> int:
    rows = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type.in_(calc_types),
            QuotationCalculationTrace.run_id.is_(None),
        )
        .all()
    )
    for row in rows:
        row.run_id = run.id
        row.bpm_instance_id = instance.id if instance else None
        _patch_trace_identity(row)
    return len(rows)


def _patch_trace_identity(row: QuotationCalculationTrace) -> None:
    input_data = _load_json(row.input_data)
    process_fee_id = input_data.get("process_fee_id")
    material_id = row.material_id or input_data.get("material_id")

    if row.field_name in {"process_amount", "process_subtotal_fee"} and process_fee_id:
        row.entity_type = "process"
        row.entity_id = _int_or_none(process_fee_id)
        target_id = row.entity_id
    elif material_id:
        row.entity_type = "material"
        row.entity_id = _int_or_none(material_id)
        target_id = row.entity_id
    else:
        row.entity_type = "main"
        row.entity_id = row.quotation_main_id
        target_id = row.entity_id

    row.display_label = DISPLAY_LABELS.get(row.field_name, row.field_name)
    row.skill_id = SKILL_BY_CALC_TYPE.get(row.calc_type, row.calc_type)
    row.cell_key = f"{row.entity_type}:{target_id}:{row.field_name}"
    row.source_refs = json.dumps(_source_refs(input_data), ensure_ascii=False)


def _source_refs(input_data: dict) -> list[dict]:
    refs = []
    for key, value in input_data.items():
        if value in (None, ""):
            continue
        if key.endswith("_id"):
            continue
        refs.append({"name": key, "value": str(value)})
    return refs


def _params_snapshot(instance: QuotationBpmInstance | None) -> dict:
    if not instance:
        return {}
    return {
        "bpm_no": instance.bpm_no or "",
        "quote_date": instance.quote_date.isoformat() if instance.quote_date else None,
        "copper_price": _decimal_text(instance.copper_price),
        "copper_rod_process_fee": _decimal_text(instance.copper_rod_process_fee),
        "vat_rate": _decimal_text(instance.vat_rate),
    }


def _load_json(raw_value: str | None) -> dict:
    try:
        data = json.loads(raw_value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
