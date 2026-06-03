import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.quotation import QuotationMain
from app.services.excel_preview_service import REVIEW_QUOTED, get_review_status


def calculate_price_summary(db: Session, quotation: QuotationMain, operator: str) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能重新计算")

    order_meterage = _required_positive(quotation.order_meterage, "订单米数")
    net_profit_rate = _decimal(quotation.net_profit_rate)
    if net_profit_rate >= Decimal("1"):
        raise ValueError("净利率不能大于或等于 100%")

    materials = [item for item in quotation.materials if not item.deleted]
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
    vat_rate = _decimal(quotation.vat_rate)
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

    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "price_summary",
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

    db.commit()
    return {
        "cost": _decimal_text(cost),
        "profit_selling_price": _decimal_text(profit_selling_price),
        "non_profit_price": _decimal_text(non_profit_price),
        "final_selling_price": _decimal_text(final_selling_price),
    }


def list_price_summary_traces(db: Session, quotation: QuotationMain) -> list[dict]:
    rows = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type == "price_summary",
        )
        .order_by(QuotationCalculationTrace.create_time.desc(), QuotationCalculationTrace.id.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": row.id,
            "field_name": row.field_name,
            "formula": row.formula,
            "input_data": json.loads(row.input_data) if row.input_data else {},
            "process_text": row.process_text or "",
            "result_value": _decimal_text(row.result_value),
            "operator": row.operator,
            "create_time": row.create_time.isoformat() if row.create_time else None,
        }
        for row in rows
    ]


def _add_trace(db, quotation, field_name, formula, input_data, process_text, result_value, operator):
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=None,
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
