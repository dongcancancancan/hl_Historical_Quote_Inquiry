import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.quotation import QuotationMain
from app.services.calculation_context import CalculationContext
from app.services.calc_param_service import normalize_vat_rate
from app.services.excel_preview_service import REVIEW_QUOTED, get_review_status
from app.services.internal_metric_service import calculate_internal_metrics
from app.services.quotation_summary_service import apply_quotation_summaries


def calculate_price_summary(
    db: Session,
    quotation: QuotationMain,
    operator: str,
    ctx: CalculationContext | None = None,
    commit: bool = True,
) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能重新计算")

    order_meterage = _required_positive(quotation.order_meterage, "订单米数")
    net_profit_rate = _decimal(quotation.net_profit_rate)
    if net_profit_rate >= Decimal("1"):
        raise ValueError("净利率不能大于或等于 100%")

    materials = [item for item in quotation.materials if not item.deleted]
    processes = [item for item in quotation.processes if not item.deleted]
    if ctx:
        _validate_current_calculation_context(materials, processes, ctx)

    apply_quotation_summaries(quotation)
    calculated_unit_usage_sum = sum((_decimal(item.unit_usage) for item in materials), Decimal("0")) / Decimal("100")
    calculated_material_amount_sum = sum((_decimal(item.material_amount) for item in materials), Decimal("0"))
    total_fee = _decimal(quotation.total_fee)
    material_cost = _round4(calculated_material_amount_sum / Decimal("100"))
    waste_loss_rate = _decimal(quotation.waste_loss_rate)
    ul_label_fee = _decimal(quotation.ul_label_fee)
    packing_fee = _decimal(quotation.packing_fee)
    customs_fee = _decimal(quotation.customs_fee)
    other_fee = _decimal(quotation.other_fee)
    irradiation_core_count = _decimal(quotation.irradiation_core_count)
    irradiation_core_fee = _decimal(quotation.irradiation_core_fee)
    transport_fee = _decimal(quotation.transport_fee)
    unit_usage_sum = _round4(calculated_unit_usage_sum)
    vat_rate = normalize_vat_rate(quotation.vat_rate) if quotation.vat_rate is not None else Decimal("0")
    operating_expense_rate = _decimal(quotation.operating_expense_rate)
    monthly_interest = _decimal(quotation.monthly_interest)
    corporate_tax_rate = _decimal(quotation.corporate_tax_rate)

    process_cost_per_meter = total_fee / order_meterage
    scrap_cost = material_cost * waste_loss_rate
    cost = _round4(process_cost_per_meter + material_cost + scrap_cost)

    shared_fee = (
        ul_label_fee
        + packing_fee
        + customs_fee / order_meterage
        + other_fee / order_meterage
        + irradiation_core_count * irradiation_core_fee
        + transport_fee * unit_usage_sum
    )
    tax_multiplier = Decimal("1") + vat_rate
    finance_multiplier = Decimal("1") + operating_expense_rate + monthly_interest

    profit_selling_price = _round4(
        (cost / (Decimal("1") - net_profit_rate) + shared_fee)
        * tax_multiplier
        * finance_multiplier
    )
    non_profit_price = _round4(
        (cost + shared_fee)
        * tax_multiplier
        * finance_multiplier
    )
    final_selling_price = _round4(
        profit_selling_price + (profit_selling_price - non_profit_price) * corporate_tax_rate
    )

    now = datetime.now()
    quotation.unit_usage_sum = unit_usage_sum
    quotation.material_amount_sum = _round4(calculated_material_amount_sum)
    quotation.material_cost = material_cost
    quotation.cost = cost
    quotation.profit_selling_price = profit_selling_price
    quotation.non_profit_price = non_profit_price
    quotation.final_selling_price = final_selling_price
    quotation.updater = operator
    quotation.update_time = now
    internal_metrics = calculate_internal_metrics(quotation)

    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "price_summary",
        QuotationCalculationTrace.run_id.is_(None),
    ).delete(synchronize_session=False)

    input_data = {
        "total_fee": str(total_fee),
        "order_meterage": str(order_meterage),
        "material_cost": str(material_cost),
        "waste_loss_rate": str(waste_loss_rate),
        "ul_label_fee": str(ul_label_fee),
        "packing_fee": str(packing_fee),
        "customs_fee": str(customs_fee),
        "other_fee": str(other_fee),
        "irradiation_core_count": str(irradiation_core_count),
        "irradiation_core_fee": str(irradiation_core_fee),
        "transport_fee": str(transport_fee),
        "unit_usage_sum": str(unit_usage_sum),
        "net_profit_rate": str(net_profit_rate),
        "vat_rate": str(vat_rate),
        "operating_expense_rate": str(operating_expense_rate),
        "monthly_interest": str(monthly_interest),
        "corporate_tax_rate": str(corporate_tax_rate),
        "shared_fee": str(_round4(shared_fee)),
    }
    _add_trace(
        db,
        quotation,
        "cost",
        "成本 = 费用总计 / 订单米数 + 材料成本 + 材料成本 × 废品损耗",
        input_data,
        (
            f"成本 = {total_fee} / {order_meterage} + {material_cost} + {material_cost} × {waste_loss_rate}"
            f" = {cost}"
        ),
        cost,
        operator,
    )
    _add_trace(
        db,
        quotation,
        "profit_selling_price",
        "取利售价 = (成本 / (1 - 净利率) + 共享费用) × (1 + 增值税率) × (1 + 营业费用率 + 月结利息)",
        input_data,
        (
            f"共享费用 = {ul_label_fee} + {packing_fee} + {customs_fee}/{order_meterage}"
            f" + {other_fee}/{order_meterage} + {irradiation_core_count}×{irradiation_core_fee}"
            f" + {transport_fee}×{unit_usage_sum} = {_round4(shared_fee)}\n"
            f"取利售价 = ({cost} / (1 - {net_profit_rate}) + {_round4(shared_fee)})"
            f" × (1 + {vat_rate}) × (1 + {operating_expense_rate} + {monthly_interest}) = {profit_selling_price}"
        ),
        profit_selling_price,
        operator,
    )
    _add_trace(
        db,
        quotation,
        "non_profit_price",
        "不取利售价 = (成本 + 共享费用) × (1 + 增值税率) × (1 + 营业费用率 + 月结利息)",
        input_data,
        (
            f"不取利售价 = ({cost} + {_round4(shared_fee)})"
            f" × (1 + {vat_rate}) × (1 + {operating_expense_rate} + {monthly_interest}) = {non_profit_price}"
        ),
        non_profit_price,
        operator,
    )
    _add_trace(
        db,
        quotation,
        "final_selling_price",
        "最终售价 = 取利售价 + (取利售价 - 不取利售价) × 企税税率",
        input_data,
        (
            f"最终售价 = {profit_selling_price} + ({profit_selling_price} - {non_profit_price})"
            f" × {corporate_tax_rate} = {final_selling_price}"
        ),
        final_selling_price,
        operator,
    )

    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "cost": _decimal_text(cost),
        "profit_selling_price": _decimal_text(profit_selling_price),
        "non_profit_price": _decimal_text(non_profit_price),
        "final_selling_price": _decimal_text(final_selling_price),
        "material_ratio": _decimal_text(internal_metrics["material_ratio"]),
        "order_weight": _decimal_text(internal_metrics["order_weight"]),
    }


def list_price_summary_traces(
    db: Session,
    quotation: QuotationMain,
    bpm_instance_id: int | None = None,
    run_id: int | None = None,
) -> list[dict]:
    query = db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "price_summary",
    )
    if run_id:
        query = query.filter(QuotationCalculationTrace.run_id == run_id)
    elif bpm_instance_id:
        query = query.filter(QuotationCalculationTrace.bpm_instance_id == bpm_instance_id)
    rows = query.order_by(QuotationCalculationTrace.create_time.desc(), QuotationCalculationTrace.id.desc()).limit(100).all()
    rows = _dedupe_price_summary_rows(rows)
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "bpm_instance_id": row.bpm_instance_id,
            "entity_type": row.entity_type or "",
            "entity_id": row.entity_id,
            "field_name": row.field_name,
            "display_label": row.display_label or "",
            "cell_key": row.cell_key or "",
            "skill_id": row.skill_id or "",
            "formula": row.formula,
            "input_data": json.loads(row.input_data) if row.input_data else {},
            "source_refs": json.loads(row.source_refs) if row.source_refs else [],
            "process_text": row.process_text or "",
            "result_value": _decimal_text(row.result_value),
            "operator": row.operator,
            "create_time": row.create_time.isoformat() if row.create_time else None,
        }
        for row in rows
    ]


def _dedupe_price_summary_rows(rows: list[QuotationCalculationTrace]) -> list[QuotationCalculationTrace]:
    order = ["cost", "profit_selling_price", "non_profit_price", "final_selling_price"]
    latest_by_field = {}
    for row in rows:
        latest_by_field.setdefault(row.field_name, row)
    return [latest_by_field[field] for field in order if field in latest_by_field]


def _validate_current_calculation_context(materials, processes, ctx: CalculationContext):
    missing_materials = [
        item for item in materials
        if not _is_blank_material_row(item) and item.id not in ctx.calculated_material_ids
    ]
    missing_processes = [
        item for item in processes
        if not _is_blank_process_row(item) and item.id not in ctx.calculated_process_ids
    ]
    messages = []
    if missing_materials:
        messages.append(
            "以下材料行未完成本次计算，旧材料金额不会参与最终售价："
            + _join_row_labels(missing_materials, _material_label)
        )
    if missing_processes:
        messages.append(
            "以下制程费用行未完成本次计算，旧费用小计不会参与最终售价："
            + _join_row_labels(missing_processes, _process_label)
        )
    if messages:
        raise ValueError("；".join(messages) + "。请维护价格/公式，或手填单价后重新一键计算")


def _join_row_labels(rows, labeler, limit: int = 8) -> str:
    labels = [labeler(row) for row in rows[:limit]]
    if len(rows) > limit:
        labels.append(f"等 {len(rows)} 行")
    return "、".join(labels)


def _material_label(item) -> str:
    name = item.process_name or "物料"
    code = item.process_code or item.spec_detail or "-"
    seq = item.seq_no or "-"
    return f"{seq}.{name}（{code}）"


def _process_label(item) -> str:
    return item.process_name or f"制程费用ID {item.id}"


def _is_blank_material_row(item) -> bool:
    text_empty = not any(str(value or "").strip() for value in (item.process_name, item.spec_detail, item.process_code))
    numbers_empty = all(_decimal(value) == 0 for value in (item.unit_usage, item.unit_price, item.material_amount))
    return text_empty and numbers_empty


def _is_blank_process_row(item) -> bool:
    text_empty = not str(item.process_name or "").strip()
    numbers_empty = all(
        _decimal(value) == 0
        for value in (
            item.std_hours,
            item.loss_hours,
            item.fixed_rate,
            item.fixed_fee,
            item.startup_loss_wire,
            item.total_waste_glue,
            item.amount,
            item.subtotal_fee,
        )
    )
    return text_empty and numbers_empty


def _add_trace(db, quotation, field_name, formula, input_data, process_text, result_value, operator):
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=None,
        entity_type="main",
        entity_id=quotation.id,
        calc_type="price_summary",
        field_name=field_name,
        formula=formula,
        input_data=json.dumps(input_data, ensure_ascii=False),
        process_text=process_text,
        result_value=result_value,
        operator=operator,
    ))


def _required_positive(value, label: str) -> Decimal:
    result = _decimal(value)
    if result <= 0:
        raise ValueError(f"{label}必须大于 0")
    return result


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
