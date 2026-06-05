import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.quotation import QuotationMain, QuotationMaterial
from app.services.calculation_context import CalculationContext
from app.services.conductor_calc_service import _is_conductor_row
from app.services.excel_preview_service import REVIEW_QUOTED, get_review_status
from app.services.unit_price_override_service import apply_unit_price_overrides, has_unit_price_override, load_unit_price_overrides


PVC_CODE_RE = re.compile(r"\b(C[A-Z0-9*]{3,})\b", re.IGNORECASE)
GLUE_CALC_TYPES = ["glue", "external_material", "manual_material", "insulation", "jacket", "rewind", "collection"]


def calculate_glue_materials(
    db: Session,
    quotation: QuotationMain,
    operator: str,
    ctx: CalculationContext | None = None,
    commit: bool = True,
) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能重新计算")
    unit_price_overrides = load_unit_price_overrides(db, quotation.id)
    apply_unit_price_overrides(quotation, unit_price_overrides)

    candidates = [
        item for item in quotation.materials
        if not item.deleted
        and not _is_conductor_row(item)
        and (
            _has_c_code(item)
            or _external_material_code(item)
            or _is_jacket_row(item)
            or _is_color_masterbatch_row(item)
            or item.id in unit_price_overrides
        )
    ]
    rewind_processes = [item for item in quotation.processes if not item.deleted and _is_rewind_process(item)]
    collection_processes = [item for item in quotation.processes if not item.deleted and _is_collection_process(item)]
    if not candidates and not rewind_processes and not collection_processes:
        raise ValueError("未找到可计算的 C 开头胶料、外购物料、外被、倒线或集合制程行")

    calculated = 0
    c_calculated = 0
    external_calculated = 0
    manual_material_calculated = 0
    color_masterbatch_calculated = 0
    insulation_process_calculated = 0
    jacket_process_calculated = 0
    rewind_process_calculated = 0
    collection_process_calculated = 0
    skipped = []
    hard_errors = []
    now = datetime.now()

    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type.in_(GLUE_CALC_TYPES),
    ).delete(synchronize_session=False)

    for item in candidates:
        material_calculated = False
        if has_unit_price_override(item, unit_price_overrides):
            manual_ok = _calculate_manual_price_material(db, quotation, item, operator, now, ctx)
            if manual_ok:
                manual_material_calculated += 1
                if _is_color_masterbatch_row(item):
                    color_masterbatch_calculated += 1
                material_calculated = True
            else:
                skipped.append({
                    "id": item.id,
                    "reason": f"{item.process_name or '物料'}已设置手工单价但单价为空或无效",
                })
                continue
        elif _is_color_masterbatch_row(item):
            ok = False
            if _external_material_code(item):
                ok = _calculate_external_material(db, quotation, item, operator, now, ctx=ctx)
                if ok:
                    external_calculated += 1
                    color_masterbatch_calculated += 1
                    material_calculated = True
            if not ok:
                skipped.append({
                    "id": item.id,
                    "reason": _missing_color_masterbatch_price_message(item),
                })
                continue
        elif _has_c_code(item):
            ok = _calculate_c_material(db, quotation, item, operator, now, ctx)
            if ok:
                c_calculated += 1
                material_calculated = True
            else:
                ok = _calculate_external_material(db, quotation, item, operator, now, allow_c_code=True, ctx=ctx)
                if ok:
                    external_calculated += 1
                    material_calculated = True
                else:
                    skipped.append({"id": item.id, "reason": _missing_pvc_and_external_price_message(item)})
                    continue
        elif _external_material_code(item):
            ok = _calculate_external_material(db, quotation, item, operator, now, ctx=ctx)
            if ok:
                external_calculated += 1
                material_calculated = True
            else:
                skipped.append({
                    "id": item.id,
                    "reason": _missing_external_price_message(item),
                })
                continue
        if material_calculated:
            calculated += 1
        if _is_insulation_row(item):
            ok, error = _calculate_insulation_process_fee(db, quotation, item, operator, now, ctx)
            if ok:
                insulation_process_calculated += 1
            elif error:
                hard_errors.append(error)

    for item in [row for row in quotation.materials if not row.deleted and _is_jacket_row(row)]:
        ok, error = _calculate_jacket_process_fee(db, quotation, item, operator, now, ctx)
        if ok:
            jacket_process_calculated += 1
        elif error:
            hard_errors.append(error)

    for process in rewind_processes:
        ok, error = _calculate_rewind_process_fee(db, quotation, process, operator, now, ctx)
        if ok:
            rewind_process_calculated += 1
        elif error:
            hard_errors.append(error)

    for process in collection_processes:
        ok, error = _calculate_collection_process_fee(db, quotation, process, operator, now, ctx)
        if ok:
            collection_process_calculated += 1
        elif error:
            hard_errors.append(error)

    if (
        calculated == 0
        and insulation_process_calculated == 0
        and jacket_process_calculated == 0
        and rewind_process_calculated == 0
        and collection_process_calculated == 0
    ):
        message = "；".join(item["reason"] for item in skipped) or "没有可计算的胶料、外购物料、外被、倒线或集合制程行"
        raise ValueError(message)
    if hard_errors:
        skipped_messages = [item["reason"] for item in skipped]
        raise ValueError("；".join(skipped_messages + hard_errors))
    if skipped:
        raise ValueError("；".join(item["reason"] for item in skipped))

    _recalculate_material_summary(quotation, operator, now)
    _recalculate_process_summary(quotation, operator, now)
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "calculated": calculated,
        "c_calculated": c_calculated,
        "external_calculated": external_calculated,
        "manual_material_calculated": manual_material_calculated,
        "color_masterbatch_calculated": color_masterbatch_calculated,
        "insulation_process_calculated": insulation_process_calculated,
        "jacket_process_calculated": jacket_process_calculated,
        "rewind_process_calculated": rewind_process_calculated,
        "collection_process_calculated": collection_process_calculated,
        "skipped": skipped,
    }


def list_glue_traces(db: Session, quotation: QuotationMain) -> list[dict]:
    rows = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type.in_(GLUE_CALC_TYPES),
        )
        .order_by(QuotationCalculationTrace.create_time.desc(), QuotationCalculationTrace.id.desc())
        .limit(300)
        .all()
    )
    return [
        {
            "id": row.id,
            "material_id": row.material_id,
            "calc_type": row.calc_type,
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


def _calculate_c_material(
    db: Session,
    quotation: QuotationMain,
    item: QuotationMaterial,
    operator: str,
    now: datetime,
    ctx: CalculationContext | None = None,
) -> bool:
    lookup = _resolve_pvc_bom(db, item)
    if not lookup:
        return False

    unit_price = _round4(lookup["saleprice"])
    material_amount = _round4(Decimal(item.unit_usage or 0) * unit_price)
    item.unit_price = unit_price
    item.material_amount = material_amount
    item.updater = operator
    item.update_time = now
    if ctx:
        ctx.mark_material(item.id, "pvc_bom")

    input_data = {
        "material_id": item.id,
        "process_name": item.process_name,
        "spec_detail": item.spec_detail,
        "process_code": item.process_code,
        "resolved_bom_no": lookup["bom_no"],
        "source_text": lookup["source_text"],
        "source": "PVC_BOM_Main.saleprice",
        "match_mode": lookup.get("match_mode", "exact"),
        "lookup_pattern": lookup.get("lookup_pattern"),
        "saleprice": str(lookup["saleprice"]),
        "unit_usage": str(item.unit_usage or 0),
    }
    match_text = ""
    if lookup.get("match_mode") == "wildcard_highest":
        match_text = f"按通配料号 {lookup.get('lookup_pattern')} 模糊匹配并取最高售价。\n"
    process_text = (
        f"{match_text}"
        f"从 {lookup['source_text']} 解析并匹配到 PVC 母料 {lookup['bom_no']}，该母料售价 = {lookup['saleprice']}。\n"
        f"胶料单价 = PVC 母料售价 = {unit_price}\n"
        f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
    )
    _add_trace(db, quotation, item, "unit_price", "胶料单价 = PVC 母料 BOM 售价", input_data, process_text, unit_price, operator, "glue")
    _add_trace(db, quotation, item, "material_amount", "材料金额 = BOM用量 × 胶料单价", input_data, process_text, material_amount, operator, "glue")
    return True


def _calculate_external_material(
    db: Session,
    quotation: QuotationMain,
    item: QuotationMaterial,
    operator: str,
    now: datetime,
    allow_c_code: bool = False,
    ctx: CalculationContext | None = None,
) -> bool:
    material_code = _external_material_code(item, allow_c_code=allow_c_code)
    if not material_code:
        return False
    lookup = _resolve_external_material_price(db, material_code)
    if not lookup:
        return False

    unit_price = _round4(lookup["unit_standard_cost"])
    material_amount = _round4(Decimal(item.unit_usage or 0) * unit_price)
    item.unit_price = unit_price
    item.material_amount = material_amount
    item.updater = operator
    item.update_time = now
    if ctx:
        ctx.mark_material(item.id, "external_material")

    adjust_date = lookup["adjust_date"].isoformat() if lookup["adjust_date"] else ""
    input_data = {
        "material_id": item.id,
        "process_name": item.process_name,
        "spec_detail": item.spec_detail,
        "process_code": item.process_code,
        "material_code": material_code,
        "source_view": "HL_QS.dbo.v_qs_bzcb",
        "price_field": "单位标准成本",
        "match_mode": lookup.get("match_mode", "exact"),
        "lookup_pattern": lookup.get("lookup_pattern"),
        "adjust_date": adjust_date,
        "unit": lookup["unit"],
        "unit_standard_cost": str(lookup["unit_standard_cost"]),
        "material_description": lookup["material_description"],
        "unit_usage": str(item.unit_usage or 0),
    }
    match_text = ""
    if lookup.get("match_mode") == "wildcard_highest":
        match_text = f"按通配料号 {lookup.get('lookup_pattern')} 模糊匹配到 {lookup['material_code']}，并取最高单位标准成本。\n"
    process_text = (
        f"{match_text}"
        f"物料编号 {material_code} 命中 HL_QS.dbo.v_qs_bzcb，最新调整日期 {adjust_date or '-'}，"
        f"单位标准成本 {lookup['unit_standard_cost']}，作为单价。\n"
        f"外购物料单价 = 单位标准成本 = {unit_price}\n"
        f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
    )
    _add_trace(
        db,
        quotation,
        item,
        "unit_price",
        "外购物料单价 = v_qs_bzcb 最新调整日期的单位标准成本",
        input_data,
        process_text,
        unit_price,
        operator,
        "external_material",
    )
    _add_trace(
        db,
        quotation,
        item,
        "material_amount",
        "材料金额 = BOM用量 × 外购物料单价",
        input_data,
        process_text,
        material_amount,
        operator,
        "external_material",
    )
    return True


def _calculate_manual_price_material(
    db: Session,
    quotation: QuotationMain,
    item: QuotationMaterial,
    operator: str,
    now: datetime,
    ctx: CalculationContext | None = None,
) -> bool:
    unit_price = Decimal(item.unit_price or 0)
    material_code = _external_material_code(item, allow_c_code=True) or item.process_code or item.spec_detail or ""
    if unit_price <= 0:
        return False

    material_amount = _round4(Decimal(item.unit_usage or 0) * unit_price)
    item.material_amount = material_amount
    item.updater = operator
    item.update_time = now
    if ctx:
        ctx.mark_material(item.id, "manual_unit_price")

    input_data = {
        "material_id": item.id,
        "process_name": item.process_name,
        "spec_detail": item.spec_detail,
        "process_code": item.process_code,
        "source": "手填单价",
        "unit_usage": str(item.unit_usage or 0),
        "unit_price": str(unit_price),
        "material_amount": str(material_amount),
        "missing_external_price": material_code,
    }
    process_text = (
        f"物料编号 {material_code} 未在价格源命中，使用审价人员手填单价 {unit_price}。\n"
        f"材料金额 = BOM用量 {item.unit_usage or 0} × 手填单价 {unit_price} = {material_amount}"
    )
    _add_trace(
        db,
        quotation,
        item,
        "material_amount",
        "材料金额 = BOM用量 × 手填单价",
        input_data,
        process_text,
        material_amount,
        operator,
        "manual_material",
    )
    return True


def _calculate_insulation_process_fee(
    db: Session,
    quotation: QuotationMain,
    item: QuotationMaterial,
    operator: str,
    now: datetime,
    ctx: CalculationContext | None = None,
) -> tuple[bool, str]:
    process = _match_insulation_process_fee_row(quotation, item)
    if not process:
        return False, f"未找到绝缘/芯押对应的制程费用行：{item.process_name or ''}"

    conductor_amount = _conductor_material_amount_before(quotation, item, ctx)
    if conductor_amount <= 0:
        return False, "绝缘制程计算需要先计算导体/铜绞材料金额"

    insulation_amount = Decimal(item.material_amount or 0)
    insulation_unit_price = Decimal(item.unit_price or 0)
    startup_loss_wire = Decimal(process.startup_loss_wire or 0)
    total_waste_glue = Decimal(process.total_waste_glue or 0)
    fixed_fee = Decimal(process.fixed_fee or 0)

    process_amount = _round4(
        startup_loss_wire * (conductor_amount + insulation_amount)
        + total_waste_glue * insulation_unit_price
    )
    subtotal_fee = _round4(fixed_fee + process_amount * total_waste_glue)

    process.amount = process_amount
    process.subtotal_fee = subtotal_fee
    process.updater = operator
    process.update_time = now
    if ctx:
        ctx.mark_process(process.id, "insulation")

    input_data = {
        "material_id": item.id,
        "process_fee_id": process.id,
        "material_process_name": item.process_name,
        "process_fee_name": process.process_name,
        "conductor_material_amount": str(conductor_amount),
        "insulation_material_amount": str(insulation_amount),
        "insulation_unit_price": str(insulation_unit_price),
        "startup_loss_wire": str(startup_loss_wire),
        "total_waste_glue": str(total_waste_glue),
        "fixed_fee": str(fixed_fee),
    }
    process_text = (
        f"匹配绝缘制程费用行：{process.process_name or item.process_name or ''}\n"
        f"金额 = 开机损耗废线 {startup_loss_wire} × (导体绞合材料金额 {conductor_amount} + 绝缘材料金额 {insulation_amount})"
        f" + 每个制程总废胶 {total_waste_glue} × 绝缘单价 {insulation_unit_price} = {process_amount}\n"
        f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 每个制程总废胶 {total_waste_glue} = {subtotal_fee}"
    )
    _add_trace(
        db,
        quotation,
        item,
        "process_amount",
        "绝缘金额 = 开机损耗废线 × (导体绞合材料金额 + 绝缘材料金额) + 每个制程总废胶 × 绝缘单价",
        input_data,
        process_text,
        process_amount,
        operator,
        "insulation",
    )
    _add_trace(
        db,
        quotation,
        item,
        "process_subtotal_fee",
        "绝缘费用成本小计 = 固定费用 + 金额 × 每个制程总废胶",
        input_data,
        process_text,
        subtotal_fee,
        operator,
        "insulation",
    )
    return True, ""


def _calculate_jacket_process_fee(
    db: Session,
    quotation: QuotationMain,
    item: QuotationMaterial,
    operator: str,
    now: datetime,
    ctx: CalculationContext | None = None,
) -> tuple[bool, str]:
    process = _match_jacket_process_fee_row(quotation, item)
    if not process:
        return False, f"未找到外被对应的制程费用行：{item.process_name or ''}"

    material_amount_sum = _material_amount_sum(quotation, ctx)
    if material_amount_sum <= 0:
        return False, "外被制程计算需要先计算材料金额总和"

    jacket_unit_price = Decimal(item.unit_price or 0)
    if jacket_unit_price <= 0:
        return False, f"外被制程计算需要先计算或填写外被单价：{item.process_name or ''}"

    startup_loss_wire = Decimal(process.startup_loss_wire or 0)
    total_waste_glue = Decimal(process.total_waste_glue or 0)
    fixed_fee = Decimal(process.fixed_fee or 0)
    startup_fee = Decimal(quotation.order_startup_times or 0)

    process_amount = _round4(
        startup_loss_wire * material_amount_sum
        + total_waste_glue * jacket_unit_price
    )
    subtotal_fee = _round4(fixed_fee + process_amount * startup_fee)

    process.amount = process_amount
    process.subtotal_fee = subtotal_fee
    process.updater = operator
    process.update_time = now
    if ctx:
        ctx.mark_process(process.id, "jacket")

    input_data = {
        "material_id": item.id,
        "process_fee_id": process.id,
        "material_process_name": item.process_name,
        "process_fee_name": process.process_name,
        "material_amount_sum": str(material_amount_sum),
        "jacket_unit_price": str(jacket_unit_price),
        "startup_loss_wire": str(startup_loss_wire),
        "total_waste_glue": str(total_waste_glue),
        "fixed_fee": str(fixed_fee),
        "startup_fee": str(startup_fee),
        "startup_fee_source": "quotation_main.order_startup_times",
    }
    process_text = (
        f"匹配外被制程费用行：{process.process_name or item.process_name or ''}\n"
        f"金额 = 开机损耗废线 {startup_loss_wire} × 材料金额总和 {material_amount_sum}"
        f" + 每个制程总废胶 {total_waste_glue} × 外被单价 {jacket_unit_price} = {process_amount}\n"
        f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 开机费用 {startup_fee} = {subtotal_fee}"
    )
    _add_trace(
        db,
        quotation,
        item,
        "process_amount",
        "外被金额 = 开机损耗废线 × 材料金额总和 + 每个制程总废胶 × 外被单价",
        input_data,
        process_text,
        process_amount,
        operator,
        "jacket",
    )
    _add_trace(
        db,
        quotation,
        item,
        "process_subtotal_fee",
        "外被费用成本小计 = 固定费用 + 金额 × 开机费用",
        input_data,
        process_text,
        subtotal_fee,
        operator,
        "jacket",
    )
    return True, ""


def _calculate_rewind_process_fee(
    db: Session,
    quotation: QuotationMain,
    process,
    operator: str,
    now: datetime,
    ctx: CalculationContext | None = None,
) -> tuple[bool, str]:
    material_amount_sum = _material_amount_sum(quotation, ctx)
    if material_amount_sum <= 0:
        return False, "倒线制程计算需要先计算材料金额总和"

    startup_loss_wire = Decimal(process.startup_loss_wire or 0)
    fixed_fee = Decimal(process.fixed_fee or 0)
    startup_times = Decimal(quotation.order_startup_times or 0)

    process_amount = _round4(startup_loss_wire * material_amount_sum)
    subtotal_fee = _round4(fixed_fee + process_amount * startup_times)

    process.amount = process_amount
    process.subtotal_fee = subtotal_fee
    process.updater = operator
    process.update_time = now
    if ctx:
        ctx.mark_process(process.id, "rewind")

    input_data = {
        "process_fee_id": process.id,
        "process_fee_name": process.process_name,
        "material_amount_sum": str(material_amount_sum),
        "startup_loss_wire": str(startup_loss_wire),
        "fixed_fee": str(fixed_fee),
        "startup_times": str(startup_times),
        "startup_times_source": "quotation_main.order_startup_times",
    }
    process_text = (
        f"匹配倒线制程费用行：{process.process_name or ''}\n"
        f"金额 = 开机损耗废线 {startup_loss_wire} × 材料金额总和 {material_amount_sum} = {process_amount}\n"
        f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 订单开机次数 {startup_times} = {subtotal_fee}"
    )
    _add_trace(
        db,
        quotation,
        None,
        "process_amount",
        "倒线金额 = 开机损耗废线 × 材料金额总和",
        input_data,
        process_text,
        process_amount,
        operator,
        "rewind",
    )
    _add_trace(
        db,
        quotation,
        None,
        "process_subtotal_fee",
        "倒线费用成本小计 = 固定费用 + 金额 × 订单开机次数",
        input_data,
        process_text,
        subtotal_fee,
        operator,
        "rewind",
    )
    return True, ""


def _calculate_collection_process_fee(
    db: Session,
    quotation: QuotationMain,
    process,
    operator: str,
    now: datetime,
    ctx: CalculationContext | None = None,
) -> tuple[bool, str]:
    amounts = _collection_material_amounts(quotation, ctx)
    if amounts["copper_amount"] <= 0:
        return False, _missing_collection_amount_message(quotation, "铜绞", _is_core_conductor_row)
    if amounts["core_press_amount"] <= 0:
        return False, _missing_collection_amount_message(quotation, "芯押", _is_insulation_row)
    if amounts["core_twist_amount"] <= 0:
        return False, _missing_collection_amount_message(quotation, "芯绞", _is_core_twist_row)

    startup_loss_wire = Decimal(process.startup_loss_wire or 0)
    fixed_fee = Decimal(process.fixed_fee or 0)
    startup_times = Decimal(quotation.order_startup_times or 0)
    material_amount_sum = amounts["total"]

    process_amount = _round4(startup_loss_wire * material_amount_sum)
    subtotal_fee = _round4(fixed_fee + process_amount * startup_times)

    process.amount = process_amount
    process.subtotal_fee = subtotal_fee
    process.updater = operator
    process.update_time = now
    if ctx:
        ctx.mark_process(process.id, "collection")

    input_data = {
        "process_fee_id": process.id,
        "process_fee_name": process.process_name,
        "copper_material_amount": str(amounts["copper_amount"]),
        "core_press_material_amount": str(amounts["core_press_amount"]),
        "core_twist_material_amount": str(amounts["core_twist_amount"]),
        "collection_material_amount_sum": str(material_amount_sum),
        "startup_loss_wire": str(startup_loss_wire),
        "fixed_fee": str(fixed_fee),
        "startup_times": str(startup_times),
        "startup_times_source": "quotation_main.order_startup_times",
    }
    process_text = (
        f"匹配集合制程费用行：{process.process_name or ''}\n"
        f"参与材料金额 = 铜绞 {amounts['copper_amount']} + 芯押 {amounts['core_press_amount']}"
        f" + 芯绞 {amounts['core_twist_amount']} = {material_amount_sum}\n"
        f"金额 = 开机损耗废线 {startup_loss_wire} × 参与材料金额 {material_amount_sum} = {process_amount}\n"
        f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 订单开机次数 {startup_times} = {subtotal_fee}"
    )
    _add_trace(
        db,
        quotation,
        None,
        "process_amount",
        "集合金额 = 集合开机损耗废线 × (铜绞材料金额 + 芯押材料金额 + 芯绞材料金额)",
        input_data,
        process_text,
        process_amount,
        operator,
        "collection",
    )
    _add_trace(
        db,
        quotation,
        None,
        "process_subtotal_fee",
        "集合费用成本小计 = 固定费用 + 金额 × 订单开机次数",
        input_data,
        process_text,
        subtotal_fee,
        operator,
        "collection",
    )
    return True, ""


def _has_c_code(item: QuotationMaterial) -> bool:
    return any(_candidate_codes(item))


def _resolve_pvc_bom(db: Session, item: QuotationMaterial) -> dict | None:
    for source_text, code in _candidate_codes(item):
        for normalized in _normalize_code_candidates(code):
            row = db.execute(text("""
                SELECT TOP 1 BOM_NO, saleprice
                FROM dbo.PVC_BOM_Main
                WHERE BOM_NO = :bom_no AND saleprice IS NOT NULL
            """), {"bom_no": normalized}).mappings().first()
            if row:
                return {
                    "bom_no": row["BOM_NO"],
                    "saleprice": Decimal(row["saleprice"]),
                    "source_text": source_text,
                    "match_mode": "exact",
                    "lookup_pattern": normalized,
                }
        wildcard_pattern = _wildcard_like_pattern(code)
        if wildcard_pattern:
            row = db.execute(text("""
                SELECT TOP 1 BOM_NO, saleprice
                FROM dbo.PVC_BOM_Main
                WHERE UPPER(BOM_NO) LIKE :pattern ESCAPE '\\'
                  AND saleprice IS NOT NULL
                ORDER BY saleprice DESC, BOM_NO
            """), {"pattern": wildcard_pattern}).mappings().first()
            if row:
                return {
                    "bom_no": row["BOM_NO"],
                    "saleprice": Decimal(row["saleprice"]),
                    "source_text": source_text,
                    "match_mode": "wildcard_highest",
                    "lookup_pattern": wildcard_pattern,
                }
    return None


def _resolve_external_material_price(db: Session, material_code: str) -> dict | None:
    normalized_code = str(material_code or "").strip().upper()
    row = db.execute(text("""
        SELECT TOP 1
            [物料编号],
            [物料描述],
            [单位],
            [调整日期],
            [单位标准成本]
        FROM [HL_QS].[dbo].[v_qs_bzcb]
        WHERE UPPER([物料编号]) = :material_code
          AND [单位标准成本] IS NOT NULL
        ORDER BY [调整日期] DESC
    """), {"material_code": normalized_code}).mappings().first()
    match_mode = "exact"
    lookup_pattern = normalized_code
    if not row:
        wildcard_pattern = _wildcard_like_pattern(normalized_code)
        if wildcard_pattern:
            row = db.execute(text("""
                SELECT TOP 1
                    [物料编号],
                    [物料描述],
                    [单位],
                    [调整日期],
                    [单位标准成本]
                FROM [HL_QS].[dbo].[v_qs_bzcb]
                WHERE UPPER([物料编号]) LIKE :pattern ESCAPE '\\'
                  AND [单位标准成本] IS NOT NULL
                ORDER BY [单位标准成本] DESC, [调整日期] DESC, [物料编号]
            """), {"pattern": wildcard_pattern}).mappings().first()
            match_mode = "wildcard_highest"
            lookup_pattern = wildcard_pattern
    if not row:
        return None
    return {
        "material_code": row["物料编号"],
        "material_description": row["物料描述"] or "",
        "unit": row["单位"] or "",
        "adjust_date": row["调整日期"],
        "unit_standard_cost": Decimal(row["单位标准成本"]),
        "match_mode": match_mode,
        "lookup_pattern": lookup_pattern,
    }


def _candidate_codes(item: QuotationMaterial):
    raw_code = str(item.process_code or "").strip().upper()
    if raw_code.startswith("C"):
        yield "物料编码", raw_code
    for match in PVC_CODE_RE.finditer(str(item.spec_detail or "").upper()):
        yield "规格", match.group(1)


def _normalize_code_candidates(code: str) -> list[str]:
    cleaned = str(code or "").strip().upper().replace(" ", "")
    candidates = [cleaned]
    return list(dict.fromkeys(item for item in candidates if item.startswith("C")))


def _wildcard_like_pattern(code: str) -> str:
    cleaned = str(code or "").strip().upper().replace(" ", "")
    if "*" not in cleaned:
        return ""
    chars = []
    previous_wildcard = False
    for char in cleaned:
        if char == "*":
            if not previous_wildcard:
                chars.append("%")
            previous_wildcard = True
            continue
        previous_wildcard = False
        if char in {"\\", "%", "_"}:
            chars.append("\\" + char)
        else:
            chars.append(char)
    return "".join(chars)


def _external_material_code(item: QuotationMaterial, allow_c_code: bool = False) -> str:
    if _is_conductor_row(item):
        return ""
    raw_code = str(item.process_code or "").strip().upper()
    if not raw_code or raw_code in {"新开发", "NULL", "NONE", "-"}:
        return ""
    if raw_code.startswith("C") and not allow_c_code and not _is_color_masterbatch_row(item):
        return ""
    return raw_code


def _missing_external_price_message(item: QuotationMaterial) -> str:
    code = item.process_code or item.spec_detail or ""
    process = item.process_name or "物料"
    return f"{process}（料号：{code}）未在外购价格视图 v_qs_bzcb 查到单价；请维护外购价格，或在表格中手填单价后保存再计算"


def _missing_color_masterbatch_price_message(item: QuotationMaterial) -> str:
    code = str(item.process_code or "").strip()
    if not code:
        return "色母物料编码为空；请填写物料编码，或手填单价后保存再计算"
    return _missing_external_price_message(item)


def _missing_pvc_and_external_price_message(item: QuotationMaterial) -> str:
    code = item.process_code or item.spec_detail or ""
    process = item.process_name or "物料"
    return (
        f"{process}（料号：{code}）未在 PVC 母料 BOM 和外购价格视图 v_qs_bzcb 中查到单价；"
        "请维护内部 BOM/外购价格，或在表格中手填单价后保存再计算"
    )


def _is_insulation_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return "绝缘" in name or "芯押" in name


def _is_core_twist_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return "芯绞" in name


def _is_color_masterbatch_row(item: QuotationMaterial) -> bool:
    text = f"{item.process_name or ''} {item.spec_detail or ''}"
    return "色母" in text


def _is_jacket_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return not _is_color_masterbatch_row(item) and ("外被" in name or "护套" in name or "外护" in name)


def _is_core_conductor_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return (
        "芯绞" not in name
        and "编织" not in name
        and (
            "铜" in name
            or "导体" in name
            or bool(re.search(r"(\d+(?:\.\d+)?)\s*(BC|TC)", f"{item.process_code or ''} {item.spec_detail or ''}", re.IGNORECASE))
        )
    )


def _match_insulation_process_fee_row(quotation: QuotationMain, material: QuotationMaterial):
    material_name = _normalize_process_name(material.process_name)
    processes = [item for item in quotation.processes if not item.deleted]
    if material_name:
        for process in processes:
            if _normalize_process_name(process.process_name) == material_name:
                return process
    for process in processes:
        if _is_insulation_process(process):
            return process
    return None


def _is_insulation_process(process) -> bool:
    name = str(process.process_name or "")
    return "绝缘" in name or "芯押" in name


def _match_jacket_process_fee_row(quotation: QuotationMain, material: QuotationMaterial):
    material_name = _normalize_process_name(material.process_name)
    processes = [item for item in quotation.processes if not item.deleted]
    if material_name:
        for process in processes:
            if _normalize_process_name(process.process_name) == material_name:
                return process
    for process in processes:
        if _is_jacket_process(process):
            return process
    return None


def _is_jacket_process(process) -> bool:
    name = str(process.process_name or "")
    return "外被" in name or "护套" in name or "外护" in name


def _is_rewind_process(process) -> bool:
    name = str(process.process_name or "")
    return "倒线" in name


def _is_collection_process(process) -> bool:
    name = str(process.process_name or "")
    return "集合" in name


def _normalize_process_name(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _conductor_material_amount_before(
    quotation: QuotationMain,
    insulation_item: QuotationMaterial,
    ctx: CalculationContext | None = None,
) -> Decimal:
    insulation_seq = insulation_item.seq_no or 0
    candidates = [
        item for item in quotation.materials
        if not item.deleted
        and item.id != insulation_item.id
        and (ctx is None or item.id in ctx.calculated_material_ids)
        and _is_core_conductor_row(item)
        and (not insulation_seq or not item.seq_no or item.seq_no < insulation_seq)
    ]
    if not candidates:
        candidates = [
            item for item in quotation.materials
            if not item.deleted
            and item.id != insulation_item.id
            and (ctx is None or item.id in ctx.calculated_material_ids)
            and _is_core_conductor_row(item)
        ]
    return _round4(sum(Decimal(item.material_amount or 0) for item in candidates))


def _material_amount_sum(quotation: QuotationMain, ctx: CalculationContext | None = None) -> Decimal:
    return _round4(sum(
        Decimal(item.material_amount or 0)
        for item in quotation.materials
        if not item.deleted
        and (ctx is None or item.id in ctx.calculated_material_ids)
    ))


def _collection_material_amounts(
    quotation: QuotationMain,
    ctx: CalculationContext | None = None,
) -> dict[str, Decimal]:
    copper_amount = _round4(sum(
        Decimal(item.material_amount or 0)
        for item in quotation.materials
        if not item.deleted
        and (ctx is None or item.id in ctx.calculated_material_ids)
        and _is_core_conductor_row(item)
    ))
    core_press_amount = _round4(sum(
        Decimal(item.material_amount or 0)
        for item in quotation.materials
        if not item.deleted
        and (ctx is None or item.id in ctx.calculated_material_ids)
        and _is_insulation_row(item)
    ))
    core_twist_amount = _round4(sum(
        Decimal(item.material_amount or 0)
        for item in quotation.materials
        if not item.deleted
        and (ctx is None or item.id in ctx.calculated_material_ids)
        and _is_core_twist_row(item)
    ))
    return {
        "copper_amount": copper_amount,
        "core_press_amount": core_press_amount,
        "core_twist_amount": core_twist_amount,
        "total": _round4(copper_amount + core_press_amount + core_twist_amount),
    }


def _missing_collection_amount_message(quotation: QuotationMain, label: str, matcher) -> str:
    rows = [item for item in quotation.materials if not item.deleted and matcher(item)]
    if not rows:
        return f"集合制程计算缺少{label}材料行"
    details = "、".join(
        f"{item.process_name or label}（料号：{item.process_code or item.spec_detail or '-'}，材料金额：{_decimal_text(item.material_amount)}）"
        for item in rows
    )
    return f"集合制程计算需要先计算{label}材料金额；当前{details}。请先计算对应单价，或手填单价后保存再计算"


def _add_trace(db, quotation, item, field_name, formula, input_data, process_text, result_value, operator, calc_type: str):
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=item.id if item is not None else None,
        calc_type=calc_type,
        field_name=field_name,
        formula=formula,
        input_data=json.dumps(input_data, ensure_ascii=False),
        process_text=process_text,
        result_value=result_value,
        operator=operator,
    ))


def _recalculate_material_summary(quotation: QuotationMain, operator: str, now: datetime):
    materials = [item for item in quotation.materials if not item.deleted]
    unit_usage_sum = sum(Decimal(item.unit_usage or 0) for item in materials)
    amount_sum = sum(Decimal(item.material_amount or 0) for item in materials)
    quotation.unit_usage_sum = _round4(unit_usage_sum / Decimal("100"))
    quotation.material_amount_sum = _round4(amount_sum)
    quotation.material_cost = _round4(amount_sum / Decimal("100"))
    quotation.updater = operator
    quotation.update_time = now


def _recalculate_process_summary(quotation: QuotationMain, operator: str, now: datetime):
    processes = [item for item in quotation.processes if not item.deleted]
    quotation.total_fee = _round4(sum(Decimal(item.subtotal_fee or 0) for item in processes))
    quotation.updater = operator
    quotation.update_time = now


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
