from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.calc_param import QuotationCalcParam
from app.models.quotation import QuotationMain
from app.services.calc_param_service import DEFAULT_COPPER_ROD_PROCESS_FEE, DEFAULT_VAT_RATE
from app.services.conductor_calc_service import (
    _is_conductor_row,
    _match_copper_fee,
    _match_process_fee_row,
    _parse_copper_code,
)
from app.services.excel_preview_service import get_review_status


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

    codes = _quotation_codes_by_bpm(db, bpm_no)
    quotations = []
    if codes:
        quotations = (
            db.query(QuotationMain)
            .filter(QuotationMain.deleted == False, QuotationMain.quotation_code.in_(codes))
            .order_by(QuotationMain.quotation_code)
            .all()
        )
    if not quotations:
        raise ValueError("未找到该 BPM 流程号下的成本分析表")

    bands = build_copper_bands()
    rows = []
    for quotation in quotations:
        params = (
            db.query(QuotationCalcParam)
            .filter(QuotationCalcParam.quotation_main_id == quotation.id)
            .first()
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
        rows.append({
            "quotation_code": quotation.quotation_code or "",
            "bpm_no": bpm_no,
            "customer_name": quotation.customer_name or "",
            "product_spec": quotation.product_spec or "",
            "review_status": get_review_status(quotation),
            "current_final_selling_price": _decimal_text(quotation.final_selling_price),
            "bands": result_by_band,
            "errors": sorted(set(errors)),
        })
    return {"bpm_no": bpm_no, "bands": bands, "items": rows, "mapped_codes": codes}


def _quotation_codes_by_bpm(db: Session, bpm_no: str) -> list[str]:
    rows = db.execute(text("""
        SELECT DISTINCT [成本分析号] AS quotation_code
        FROM [HL_QS].[dbo].[BPM_B015_List]
        WHERE UPPER([流水号]) = :bpm_no
          AND [成本分析号] IS NOT NULL
    """), {"bpm_no": bpm_no}).mappings().all()
    return [str(row["quotation_code"]).strip() for row in rows if str(row["quotation_code"] or "").strip()]


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
            raise ValueError(f"{item.process_name or item.id} 未解析到 BC/TC 线径")
        fee = _match_copper_fee(db, parsed["copper_type"], parsed["diameter"])
        if not fee:
            raise ValueError(f"铜加工费未维护：{parsed['diameter']}{parsed['copper_type']}")
        wire_fee = _decimal(fee.processing_fee)
        unit_price = _round4((copper_price + rod_fee) / Decimal("1000") / conductor_vat_rate + wire_fee)
        material_amount = _round4(_decimal(item.unit_usage) * unit_price)
        material_amounts[item.id] = material_amount

        process = _match_process_fee_row(quotation, item, used_process_ids)
        if process:
            used_process_ids.add(process.id)
            process_amount = _round4(_decimal(process.startup_loss_wire) * material_amount)
            process_subtotals[process.id] = _round4(
                _decimal(process.fixed_fee) + process_amount * _decimal(quotation.order_startup_times)
            )

    all_material_amount = sum(
        material_amounts.get(item.id, _decimal(item.material_amount))
        for item in quotation.materials
        if not item.deleted
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


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_text(value) -> str:
    if value is None:
        return ""
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
