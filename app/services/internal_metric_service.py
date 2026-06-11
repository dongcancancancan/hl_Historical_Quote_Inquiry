from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.services.calc_param_service import normalize_vat_rate


def calculate_internal_metrics(quotation, instance=None) -> dict[str, Decimal | None]:
    material_cost = _optional_decimal(getattr(quotation, "material_cost", None))
    unit_usage_sum = _optional_decimal(getattr(quotation, "unit_usage_sum", None))
    final_selling_price = _first_decimal(
        getattr(instance, "final_selling_price", None) if instance is not None else None,
        getattr(quotation, "final_selling_price", None),
    )
    order_meterage = _first_decimal(
        getattr(instance, "order_meterage", None) if instance is not None else None,
        getattr(quotation, "order_meterage", None),
    )
    vat_rate = _first_decimal(
        getattr(instance, "vat_rate", None) if instance is not None else None,
        getattr(quotation, "vat_rate", None),
    ) or Decimal("0")
    vat_rate = _normalize_vat_rate(vat_rate)

    material_ratio = None
    if material_cost is not None and final_selling_price and final_selling_price > 0:
        material_ratio = _round4(material_cost * (Decimal("1") + vat_rate) / final_selling_price)

    order_weight = None
    if unit_usage_sum is not None and order_meterage is not None:
        order_weight = _round4(unit_usage_sum * order_meterage)

    return {
        "material_ratio": material_ratio,
        "order_weight": order_weight,
    }


def apply_internal_metrics_to_instance(instance, quotation) -> None:
    if not instance:
        return
    metrics = calculate_internal_metrics(quotation, instance)
    instance.material_ratio = metrics["material_ratio"]
    instance.order_weight = metrics["order_weight"]


def _first_decimal(*values) -> Decimal | None:
    for value in values:
        result = _optional_decimal(value)
        if result is not None:
            return result
    return None


def _optional_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalize_vat_rate(value: Decimal) -> Decimal:
    try:
        return normalize_vat_rate(value)
    except Exception:
        return Decimal("0")


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
