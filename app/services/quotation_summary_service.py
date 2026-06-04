from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


def calculate_material_summary_values(materials) -> dict:
    active = [item for item in materials if not getattr(item, "deleted", False)]
    unit_usage_sum = sum((_decimal(item.unit_usage) for item in active), Decimal("0"))
    amount_sum = sum((_decimal(item.material_amount) for item in active), Decimal("0"))
    return {
        "unit_usage_sum": _round4(unit_usage_sum / Decimal("100")),
        "material_amount_sum": _round4(amount_sum),
        "material_cost": _round4(amount_sum / Decimal("100")),
    }


def calculate_process_total(processes) -> Decimal:
    active = [item for item in processes if not getattr(item, "deleted", False)]
    return _round4(sum((_decimal(item.subtotal_fee) for item in active), Decimal("0")))


def apply_quotation_summaries(quotation, materials=None, processes=None, updater: str | None = None, now: datetime | None = None):
    materials = list(materials) if materials is not None else list(getattr(quotation, "materials", []) or [])
    processes = list(processes) if processes is not None else list(getattr(quotation, "processes", []) or [])

    material_values = calculate_material_summary_values(materials)
    quotation.unit_usage_sum = material_values["unit_usage_sum"]
    quotation.material_amount_sum = material_values["material_amount_sum"]
    quotation.material_cost = material_values["material_cost"]
    quotation.total_fee = calculate_process_total(processes)

    if updater:
        quotation.updater = updater
        quotation.update_time = now or datetime.now()

    return {
        **material_values,
        "total_fee": quotation.total_fee,
    }


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
