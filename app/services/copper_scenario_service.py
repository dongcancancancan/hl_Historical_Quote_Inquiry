from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.calc_param import QuotationCalcParam
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.services.calc_param_service import DEFAULT_COPPER_ROD_PROCESS_FEE, DEFAULT_VAT_RATE
from app.services.conductor_calc_service import (
    _is_conductor_row,
    _match_copper_fee,
    _match_process_fee_rows,
    _parse_copper_code,
)
from app.services.excel_preview_service import get_review_status
from app.services.glue_calc_service import (
    _collection_material_amounts,
    _is_core_conductor_row,
    _is_collection_process,
    _is_core_twist_row,
    _is_insulation_row,
    _is_jacket_row,
    _is_package_tape_process,
    _is_rewind_process,
    _match_insulation_process_fee_row,
    _match_jacket_process_fee_row,
)


def build_copper_bands() -> list[dict]:
    bands = []
    lower = 68001
    upper = 70000
    while upper <= 110000:
        bands.append({
            "label": f"{lower}-{upper}",
            "copper_min": lower,
            "copper_max": upper,
            "copper_price": upper,
        })
        lower = upper + 1
        upper += 2000
    return bands


def calculate_bpm_copper_scenarios(db: Session, bpm_no: str) -> dict:
    bpm_no = (bpm_no or "").strip().upper()
    if not bpm_no:
        raise ValueError("请填写 BPM 流程号")

    db_rows = (
        db.query(QuotationMain, QuotationBpmInstance)
        .join(QuotationBpmInstance, QuotationBpmInstance.quotation_main_id == QuotationMain.id)
        .filter(
            QuotationMain.deleted == False,
            QuotationBpmInstance.deleted == False,
            QuotationBpmInstance.bpm_no == bpm_no,
        )
        .order_by(QuotationMain.quotation_code, QuotationBpmInstance.id)
        .all()
    )
    if not db_rows:
        raise ValueError("未找到该 BPM 流程号下的成本分析表")

    bands = build_copper_bands()
    result_rows = []
    for quotation, instance in db_rows:
        params = (
            db.query(QuotationCalcParam)
            .filter(QuotationCalcParam.quotation_main_id == quotation.id)
            .first()
        )
        if instance:
            params = SimpleNamespace(
                copper_rod_process_fee=instance.copper_rod_process_fee or (params.copper_rod_process_fee if params else DEFAULT_COPPER_ROD_PROCESS_FEE),
                vat_rate=instance.vat_rate or (params.vat_rate if params else DEFAULT_VAT_RATE),
            )
        result_by_band = []
        errors = []
        for band in bands:
            try:
                result_by_band.append(_calculate_one_band(db, quotation, params, Decimal(band["copper_price"])))
            except ValueError as exc:
                result_by_band.append({
                    "label": band["label"],
                    "copper_price": str(band["copper_price"]),
                    "final_selling_price": "",
                    "error": str(exc),
                })
                errors.append(str(exc))
        result_rows.append({
            "quotation_code": quotation.quotation_code or "",
            "bpm_no": bpm_no,
            "customer_name": quotation.customer_name or "",
            "product_spec": quotation.product_spec or "",
            "review_status": get_review_status(quotation, instance),
            "current_final_selling_price": _decimal_text(instance.final_selling_price or quotation.final_selling_price),
            "bands": result_by_band,
            "errors": sorted(set(errors)),
        })
    return {
        "bpm_no": bpm_no,
        "bands": bands,
        "items": result_rows,
        "mapped_codes": [quotation.quotation_code for quotation, _instance in db_rows if quotation.quotation_code],
    }


def _calculate_one_band(db: Session, quotation: QuotationMain, params, copper_price: Decimal) -> dict:
    rod_fee = _decimal(params.copper_rod_process_fee if params else DEFAULT_COPPER_ROD_PROCESS_FEE)
    conductor_vat_rate = _decimal(params.vat_rate if params else DEFAULT_VAT_RATE)
    if conductor_vat_rate <= 0:
        raise ValueError("导体计算增值税率必须大于 0")

    material_amounts = {}
    process_subtotals = {}
    used_process_ids: set[int] = set()

    conductor_rows = [item for item in quotation.materials if not item.deleted and _is_conductor_row(item)]
    if not conductor_rows:
        raise ValueError("未找到导体类材料行")

    for item in conductor_rows:
        parsed = _parse_copper_code(item)
        if not parsed:
            raise ValueError(f"{item.process_name or item.id} 未解析到 BC/TC/TD 线径")
        fee = _match_copper_fee(db, parsed["copper_type"], parsed["diameter"])
        if not fee:
            raise ValueError(f"铜加工费未维护：{parsed['diameter']}{parsed['copper_type']}")
        wire_fee = _decimal(fee.processing_fee)
        unit_price = _round4((copper_price + rod_fee) / Decimal("1000") / conductor_vat_rate + wire_fee)
        material_amount = _round4(_decimal(item.unit_usage) * unit_price)
        material_amounts[item.id] = material_amount

        for process in _match_process_fee_rows(quotation, item, used_process_ids):
            used_process_ids.add(process.id)
            process_amount = _round4(_decimal(process.startup_loss_wire) * material_amount)
            process_subtotals[process.id] = _round4(
                _decimal(process.fixed_fee) + process_amount * _decimal(quotation.order_startup_times)
            )

    for item in [row for row in quotation.materials if not row.deleted and _is_insulation_row(row)]:
        process = _match_insulation_process_fee_row(quotation, item)
        if not process:
            continue
        conductor_amount = _simulated_conductor_material_amount_before(quotation, item, material_amounts)
        if conductor_amount <= 0:
            continue
        insulation_amount = _decimal(item.material_amount)
        insulation_unit_price = _decimal(item.unit_price)
        process_amount = _round4(
            _decimal(process.startup_loss_wire) * (conductor_amount + insulation_amount)
            + _decimal(process.total_waste_glue) * insulation_unit_price
        )
        process_subtotals[process.id] = _round4(
            _decimal(process.fixed_fee) + process_amount * _decimal(process.total_waste_glue)
        )

    all_material_amount = sum(
        material_amounts.get(item.id, _decimal(item.material_amount))
        for item in quotation.materials
        if not item.deleted
    )

    for item in [row for row in quotation.materials if not row.deleted and _is_jacket_row(row)]:
        process = _match_jacket_process_fee_row(quotation, item)
        if not process:
            continue
        jacket_unit_price = _decimal(item.unit_price)
        if jacket_unit_price <= 0:
            continue
        process_amount = _round4(
            _decimal(process.startup_loss_wire) * all_material_amount
            + _decimal(process.total_waste_glue) * jacket_unit_price
        )
        process_subtotals[process.id] = _round4(
            _decimal(process.fixed_fee) + process_amount * _decimal(quotation.order_startup_times)
        )

    jacket_amount = _round4(sum(
        material_amounts.get(item.id, _decimal(item.material_amount))
        for item in quotation.materials
        if not item.deleted and _is_jacket_row(item)
    ))
    package_base_amount = _round4(all_material_amount - jacket_amount)
    for process in [row for row in quotation.processes if not row.deleted and _is_package_tape_process(row)]:
        if package_base_amount <= 0:
            continue
        process_amount = _round4(_decimal(process.startup_loss_wire) * package_base_amount)
        process_subtotals[process.id] = _round4(
            _decimal(process.fixed_fee) + process_amount * _decimal(quotation.order_startup_times)
        )

    for process in [row for row in quotation.processes if not row.deleted and _is_rewind_process(row)]:
        process_amount = _round4(_decimal(process.startup_loss_wire) * all_material_amount)
        process_subtotals[process.id] = _round4(
            _decimal(process.fixed_fee) + process_amount * _decimal(quotation.order_startup_times)
        )

    for process in [row for row in quotation.processes if not row.deleted and _is_collection_process(row)]:
        amounts = _simulated_collection_material_amounts(quotation, material_amounts)
        if (
            amounts["copper_amount"] <= 0
            or amounts["core_press_amount"] <= 0
            or (amounts["has_core_twist"] and amounts["core_twist_amount"] <= 0)
        ):
            continue
        process_amount = _round4(_decimal(process.startup_loss_wire) * amounts["total"])
        process_subtotals[process.id] = _round4(
            _decimal(process.fixed_fee) + process_amount * _decimal(quotation.order_startup_times)
        )

    unit_usage_sum = _round4(sum(_decimal(item.unit_usage) for item in quotation.materials if not item.deleted) / Decimal("100"))
    material_cost = _round4(all_material_amount / Decimal("100"))
    total_fee = _round4(sum(
        process_subtotals.get(item.id, _decimal(item.subtotal_fee))
        for item in quotation.processes
        if not item.deleted
    ))

    order_meterage = _required_positive(quotation.order_meterage, "订单米数")
    net_profit_rate = _decimal(quotation.net_profit_rate)
    if net_profit_rate >= Decimal("1"):
        raise ValueError("净利率不能大于或等于 100%")

    cost = _round4(total_fee / order_meterage + material_cost + material_cost * _decimal(quotation.waste_loss_rate))
    shared_fee = (
        _decimal(quotation.ul_label_fee)
        + _decimal(quotation.packing_fee)
        + _decimal(quotation.customs_fee) / order_meterage
        + _decimal(quotation.other_fee) / order_meterage
        + _decimal(quotation.irradiation_core_count) * _decimal(quotation.irradiation_core_fee)
        + _decimal(quotation.transport_fee) * unit_usage_sum
    )
    tax_multiplier = Decimal("1") + _decimal(quotation.vat_rate)
    finance_multiplier = Decimal("1") + _decimal(quotation.operating_expense_rate) + _decimal(quotation.monthly_interest)
    profit_selling_price = _round4((cost / (Decimal("1") - net_profit_rate) + shared_fee) * tax_multiplier * finance_multiplier)
    non_profit_price = _round4((cost + shared_fee) * tax_multiplier * finance_multiplier)
    final_selling_price = _round4(
        profit_selling_price + (profit_selling_price - non_profit_price) * _decimal(quotation.corporate_tax_rate)
    )
    return {
        "copper_price": _decimal_text(copper_price),
        "cost": _decimal_text(cost),
        "profit_selling_price": _decimal_text(profit_selling_price),
        "non_profit_price": _decimal_text(non_profit_price),
        "final_selling_price": _decimal_text(final_selling_price),
        "material_cost": _decimal_text(material_cost),
        "total_fee": _decimal_text(total_fee),
        "error": "",
    }


def _required_positive(value, label: str) -> Decimal:
    result = _decimal(value)
    if result <= 0:
        raise ValueError(f"{label}必须大于 0")
    return result


def _simulated_conductor_material_amount_before(quotation: QuotationMain, insulation_item, material_amounts: dict[int, Decimal]) -> Decimal:
    insulation_seq = insulation_item.seq_no or 0
    candidates = [
        item for item in quotation.materials
        if not item.deleted
        and item.id != insulation_item.id
        and _is_core_conductor_row(item)
        and (not insulation_seq or not item.seq_no or item.seq_no < insulation_seq)
    ]
    if not candidates:
        candidates = [
            item for item in quotation.materials
            if not item.deleted and item.id != insulation_item.id and _is_core_conductor_row(item)
        ]
    return _round4(sum(material_amounts.get(item.id, _decimal(item.material_amount)) for item in candidates))


def _simulated_collection_material_amounts(quotation: QuotationMain, material_amounts: dict[int, Decimal]) -> dict[str, Decimal | bool]:
    copper_amount = _round4(sum(
        material_amounts.get(item.id, _decimal(item.material_amount))
        for item in quotation.materials
        if not item.deleted and _is_core_conductor_row(item)
    ))
    core_twist_rows = [
        item for item in quotation.materials
        if not item.deleted and _is_core_twist_row(item)
    ]
    current_amounts = _collection_material_amounts(quotation)
    core_press_amount = current_amounts["core_press_amount"]
    core_twist_amount = _round4(sum(
        _decimal(item.material_amount)
        for item in core_twist_rows
    ))
    return {
        "copper_amount": copper_amount,
        "core_press_amount": core_press_amount,
        "core_twist_amount": core_twist_amount,
        "has_core_twist": bool(core_twist_rows),
        "total": _round4(copper_amount + core_press_amount + core_twist_amount),
    }


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_text(value) -> str:
    if value is None:
        return ""
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
