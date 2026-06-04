import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.quotation import QuotationMain, QuotationMaterial
from app.services.conductor_calc_service import _is_conductor_row
from app.services.excel_preview_service import REVIEW_QUOTED, get_review_status


PVC_CODE_RE = re.compile(r"\b(C[A-Z0-9*]{3,})\b", re.IGNORECASE)


def calculate_glue_materials(db: Session, quotation: QuotationMain, operator: str) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能重新计算")

    candidates = [
        item for item in quotation.materials
        if not item.deleted
        and not _is_conductor_row(item)
        and (_has_c_code(item) or _external_material_code(item) or _is_jacket_row(item))
    ]
    if not candidates:
        raise ValueError("未找到可计算的 C 开头胶料、外购物料或外被制程行")

    calculated = 0
    c_calculated = 0
    external_calculated = 0
    insulation_process_calculated = 0
    jacket_process_calculated = 0
    skipped = []
    hard_errors = []
    now = datetime.now()

    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type.in_(["glue", "external_material", "insulation", "jacket"]),
    ).delete(synchronize_session=False)

    for item in candidates:
        material_calculated = False
        if _has_c_code(item):
            ok = _calculate_c_material(db, quotation, item, operator, now)
            if ok:
                c_calculated += 1
                material_calculated = True
            else:
                skipped.append({"id": item.id, "reason": f"未找到 PVC 母料 BOM：{item.process_code or item.spec_detail or ''}"})
                continue
        elif _external_material_code(item):
            ok = _calculate_external_material(db, quotation, item, operator, now)
            if ok:
                external_calculated += 1
                material_calculated = True
            else:
                skipped.append({"id": item.id, "reason": f"未在 v_qs_bzcb 找到外购物料最新单价：{item.process_code or item.spec_detail or ''}"})
                continue
        if material_calculated:
            calculated += 1
        if _is_insulation_row(item):
            ok, error = _calculate_insulation_process_fee(db, quotation, item, operator, now)
            if ok:
                insulation_process_calculated += 1
            elif error:
                hard_errors.append(error)

    for item in [row for row in quotation.materials if not row.deleted and _is_jacket_row(row)]:
        ok, error = _calculate_jacket_process_fee(db, quotation, item, operator, now)
        if ok:
            jacket_process_calculated += 1
        elif error:
            hard_errors.append(error)

    if calculated == 0 and insulation_process_calculated == 0 and jacket_process_calculated == 0:
        message = "；".join(item["reason"] for item in skipped) or "没有可计算的胶料、外购物料或外被制程行"
        raise ValueError(message)
    if hard_errors:
        raise ValueError("；".join(hard_errors))

    _recalculate_material_summary(quotation, operator, now)
    _recalculate_process_summary(quotation, operator, now)
    db.commit()
    return {
        "calculated": calculated,
        "c_calculated": c_calculated,
        "external_calculated": external_calculated,
        "insulation_process_calculated": insulation_process_calculated,
        "jacket_process_calculated": jacket_process_calculated,
        "skipped": skipped,
    }


def list_glue_traces(db: Session, quotation: QuotationMain) -> list[dict]:
    rows = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type.in_(["glue", "external_material", "insulation", "jacket"]),
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


def _calculate_c_material(db: Session, quotation: QuotationMain, item: QuotationMaterial, operator: str, now: datetime) -> bool:
    lookup = _resolve_pvc_bom(db, item)
    if not lookup:
        return False

    unit_price = _round4(lookup["saleprice"])
    material_amount = _round4(Decimal(item.unit_usage or 0) * unit_price)
    item.unit_price = unit_price
    item.material_amount = material_amount
    item.updater = operator
    item.update_time = now

    input_data = {
        "material_id": item.id,
        "process_name": item.process_name,
        "spec_detail": item.spec_detail,
        "process_code": item.process_code,
        "resolved_bom_no": lookup["bom_no"],
        "source_text": lookup["source_text"],
        "source": "PVC_BOM_Main.saleprice",
        "saleprice": str(lookup["saleprice"]),
        "unit_usage": str(item.unit_usage or 0),
    }
    process_text = (
        f"从 {lookup['source_text']} 解析并匹配到 PVC 母料 {lookup['bom_no']}，该母料售价 = {lookup['saleprice']}。\n"
        f"胶料单价 = PVC 母料售价 = {unit_price}\n"
        f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
    )
    _add_trace(db, quotation, item, "unit_price", "胶料单价 = PVC 母料 BOM 售价", input_data, process_text, unit_price, operator, "glue")
    _add_trace(db, quotation, item, "material_amount", "材料金额 = BOM用量 × 胶料单价", input_data, process_text, material_amount, operator, "glue")
    return True


def _calculate_external_material(db: Session, quotation: QuotationMain, item: QuotationMaterial, operator: str, now: datetime) -> bool:
    material_code = _external_material_code(item)
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

    adjust_date = lookup["adjust_date"].isoformat() if lookup["adjust_date"] else ""
    input_data = {
        "material_id": item.id,
        "process_name": item.process_name,
        "spec_detail": item.spec_detail,
        "process_code": item.process_code,
        "material_code": material_code,
        "source_view": "HL_QS.dbo.v_qs_bzcb",
        "price_field": "单位标准成本",
        "adjust_date": adjust_date,
        "unit": lookup["unit"],
        "unit_standard_cost": str(lookup["unit_standard_cost"]),
        "material_description": lookup["material_description"],
        "unit_usage": str(item.unit_usage or 0),
    }
    process_text = (
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


def _calculate_insulation_process_fee(
    db: Session,
    quotation: QuotationMain,
    item: QuotationMaterial,
    operator: str,
    now: datetime,
) -> tuple[bool, str]:
    process = _match_insulation_process_fee_row(quotation, item)
    if not process:
        return False, f"未找到绝缘/芯押对应的制程费用行：{item.process_name or ''}"

    conductor_amount = _conductor_material_amount_before(quotation, item)
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
) -> tuple[bool, str]:
    process = _match_jacket_process_fee_row(quotation, item)
    if not process:
        return False, f"未找到外被对应的制程费用行：{item.process_name or ''}"

    material_amount_sum = _material_amount_sum(quotation)
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
                return {"bom_no": row["BOM_NO"], "saleprice": Decimal(row["saleprice"]), "source_text": source_text}
    return None


def _resolve_external_material_price(db: Session, material_code: str) -> dict | None:
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
    """), {"material_code": material_code.upper()}).mappings().first()
    if not row:
        return None
    return {
        "material_code": row["物料编号"],
        "material_description": row["物料描述"] or "",
        "unit": row["单位"] or "",
        "adjust_date": row["调整日期"],
        "unit_standard_cost": Decimal(row["单位标准成本"]),
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
    if "*" in cleaned:
        candidates.append(cleaned.replace("*", "0"))
    return list(dict.fromkeys(item for item in candidates if item.startswith("C")))


def _external_material_code(item: QuotationMaterial) -> str:
    if _is_conductor_row(item):
        return ""
    raw_code = str(item.process_code or "").strip().upper()
    if not raw_code or raw_code.startswith("C") or raw_code in {"新开发", "NULL", "NONE", "-"}:
        return ""
    return raw_code


def _is_insulation_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return "绝缘" in name or "芯押" in name


def _is_jacket_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return "外被" in name or "护套" in name or "外护" in name


def _is_core_conductor_row(item: QuotationMaterial) -> bool:
    name = str(item.process_name or "")
    return ("铜" in name or "导体" in name or bool(re.search(r"(\d+(?:\.\d+)?)\s*(BC|TC)", f"{item.process_code or ''} {item.spec_detail or ''}", re.IGNORECASE))) and "编织" not in name


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


def _normalize_process_name(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _conductor_material_amount_before(quotation: QuotationMain, insulation_item: QuotationMaterial) -> Decimal:
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
    return _round4(sum(Decimal(item.material_amount or 0) for item in candidates))


def _material_amount_sum(quotation: QuotationMain) -> Decimal:
    return _round4(sum(
        Decimal(item.material_amount or 0)
        for item in quotation.materials
        if not item.deleted
    ))


def _add_trace(db, quotation, item, field_name, formula, input_data, process_text, result_value, operator, calc_type: str):
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=item.id,
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
