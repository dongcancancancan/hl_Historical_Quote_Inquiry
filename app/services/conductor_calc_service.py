import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.calc_param import QuotationCalcParam
from app.models.copper_fee import CopperProcessingFee
from app.models.quotation import QuotationMain, QuotationMaterial, QuotationProcessFee
from app.services.excel_preview_service import get_review_status, REVIEW_QUOTED


COPPER_CODE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(BC|TC)", re.IGNORECASE)
DIAMETER_TOLERANCE = Decimal("0.002")


def calculate_conductor_materials(db: Session, quotation: QuotationMain, operator: str) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能重新计算")
    params = (
        db.query(QuotationCalcParam)
        .filter(QuotationCalcParam.quotation_main_id == quotation.id)
        .first()
    )
    if not params or params.copper_price is None:
        raise ValueError("请先填写并保存铜价")

    conductor_rows = [
        item for item in quotation.materials
        if not item.deleted and _is_conductor_row(item)
    ]
    if not conductor_rows:
        raise ValueError("未找到导体类制程行")

    calculated = 0
    process_calculated = 0
    skipped = []
    used_process_ids: set[int] = set()
    now = datetime.now()
    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "conductor",
    ).delete(synchronize_session=False)

    for item in conductor_rows:
        parsed = _parse_copper_code(item)
        if not parsed:
            skipped.append({"id": item.id, "reason": "未从物料编码或规格中解析到 BC/TC 线径"})
            continue
        fee = _match_copper_fee(db, parsed["copper_type"], parsed["diameter"])
        if not fee:
            skipped.append({"id": item.id, "reason": f"铜加工费未维护：{parsed['diameter']}{parsed['copper_type']}"})
            continue

        copper_price = Decimal(params.copper_price)
        rod_fee = Decimal(params.copper_rod_process_fee)
        vat_rate = Decimal(params.vat_rate)
        wire_fee = Decimal(fee.processing_fee)
        unit_price = _round4((copper_price + rod_fee) / Decimal("1000") / vat_rate + wire_fee)
        material_amount = _round4(Decimal(item.unit_usage or 0) * unit_price)

        item.unit_price = unit_price
        item.material_amount = material_amount
        item.updater = operator
        item.update_time = now
        calculated += 1

        input_data = {
            "material_id": item.id,
            "process_name": item.process_name,
            "spec_detail": item.spec_detail,
            "process_code": item.process_code,
            "parsed_diameter": str(parsed["diameter"]),
            "parsed_copper_type": parsed["copper_type"],
            "matched_diameter": str(fee.diameter),
            "matched_copper_type": fee.copper_type,
            "copper_price": str(copper_price),
            "copper_rod_process_fee": str(rod_fee),
            "vat_rate": str(vat_rate),
            "wire_processing_fee": str(wire_fee),
            "unit_usage": str(item.unit_usage or 0),
        }
        formula = "导体单价 = (铜价 + 铜杆加工费) / 1000 / 增值税率 + 铜加工费"
        process_text = (
            f"从 {parsed['source_field']} 解析到 {parsed['diameter']}{parsed['copper_type']}；"
            f"铜加工费匹配 {fee.diameter}{fee.copper_type} = {wire_fee} 元/KG。\n"
            f"导体单价 = ({copper_price} + {rod_fee}) / 1000 / {vat_rate} + {wire_fee} = {unit_price}\n"
            f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
        )
        _add_trace(db, quotation, item, "unit_price", formula, input_data, process_text, unit_price, operator)
        _add_trace(
            db,
            quotation,
            item,
            "material_amount",
            "材料金额 = BOM用量 × 导体单价",
            input_data,
            process_text,
            material_amount,
            operator,
        )

        process = _match_process_fee_row(quotation, item, used_process_ids)
        if not process:
            skipped.append({"id": item.id, "reason": f"未找到对应的制程费用行：{item.process_name or ''}"})
            continue

        used_process_ids.add(process.id)
        startup_loss_wire = Decimal(process.startup_loss_wire or 0)
        fixed_fee = Decimal(process.fixed_fee or 0)
        startup_times = Decimal(quotation.order_startup_times or 0)
        process_amount = _round4(startup_loss_wire * material_amount)
        subtotal_fee = _round4(fixed_fee + process_amount * startup_times)

        process.amount = process_amount
        process.subtotal_fee = subtotal_fee
        process.updater = operator
        process.update_time = now
        process_calculated += 1

        process_input = dict(input_data)
        process_input.update({
            "process_fee_id": process.id,
            "process_fee_name": process.process_name,
            "startup_loss_wire": str(startup_loss_wire),
            "fixed_fee": str(fixed_fee),
            "order_startup_times": str(startup_times),
            "material_amount": str(material_amount),
        })
        fee_process_text = (
            f"匹配制程费用行：{process.process_name or item.process_name or ''}\n"
            f"金额 = 开机损耗废线 {startup_loss_wire} × 铜绞材料金额 {material_amount} = {process_amount}\n"
            f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 订单开机次数 {startup_times} = {subtotal_fee}"
        )
        _add_trace(
            db,
            quotation,
            item,
            "process_amount",
            "金额 = 开机损耗废线 × 铜绞材料金额",
            process_input,
            fee_process_text,
            process_amount,
            operator,
        )
        _add_trace(
            db,
            quotation,
            item,
            "process_subtotal_fee",
            "费用成本小计 = 固定费用 + 金额 × 订单开机次数",
            process_input,
            fee_process_text,
            subtotal_fee,
            operator,
        )

    if calculated == 0:
        message = "；".join(item["reason"] for item in skipped) or "没有可计算的导体行"
        raise ValueError(message)

    _recalculate_material_summary(quotation, operator, now)
    _recalculate_process_summary(quotation, operator, now)
    db.commit()
    return {"calculated": calculated, "process_calculated": process_calculated, "skipped": skipped}


def list_conductor_traces(db: Session, quotation: QuotationMain) -> list[dict]:
    rows = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type == "conductor",
        )
        .order_by(QuotationCalculationTrace.create_time.desc(), QuotationCalculationTrace.id.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "id": row.id,
            "material_id": row.material_id,
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


def _is_conductor_row(item: QuotationMaterial) -> bool:
    text = f"{item.process_name or ''} {item.process_code or ''} {item.spec_detail or ''}".upper()
    return "铜" in (item.process_name or "") or "导体" in (item.process_name or "") or bool(COPPER_CODE_RE.search(text))


def _match_process_fee_row(
    quotation: QuotationMain,
    material: QuotationMaterial,
    used_process_ids: set[int],
) -> QuotationProcessFee | None:
    processes = [
        item for item in quotation.processes
        if not item.deleted and item.id not in used_process_ids
    ]
    material_name = _normalize_process_name(material.process_name)
    if material_name:
        for process in processes:
            if _normalize_process_name(process.process_name) == material_name:
                return process
    for process in processes:
        if _is_conductor_process(process):
            return process
    return None


def _normalize_process_name(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _is_conductor_process(process: QuotationProcessFee) -> bool:
    name = process.process_name or ""
    return "铜" in name or "导体" in name


def _parse_copper_code(item: QuotationMaterial) -> dict | None:
    for field_name, value in (("物料编码", item.process_code), ("规格", item.spec_detail)):
        match = COPPER_CODE_RE.search(str(value or ""))
        if match:
            return {
                "diameter": Decimal(match.group(1)),
                "copper_type": match.group(2).upper(),
                "source_field": field_name,
            }
    return None


def _match_copper_fee(db: Session, copper_type: str, diameter: Decimal):
    basis = Decimal("350") if copper_type == "TC" else Decimal("0")
    exact = db.query(CopperProcessingFee).filter(
        CopperProcessingFee.copper_type == copper_type,
        CopperProcessingFee.diameter == diameter,
        CopperProcessingFee.tin_price_basis == basis,
        CopperProcessingFee.enabled == True,
    ).first()
    if exact:
        return exact
    candidates = db.query(CopperProcessingFee).filter(
        CopperProcessingFee.copper_type == copper_type,
        CopperProcessingFee.tin_price_basis == basis,
        CopperProcessingFee.enabled == True,
    ).all()
    nearest = None
    nearest_diff = None
    for candidate in candidates:
        diff = abs(Decimal(candidate.diameter) - diameter)
        if diff <= DIAMETER_TOLERANCE and (nearest_diff is None or diff < nearest_diff):
            nearest = candidate
            nearest_diff = diff
    return nearest


def _add_trace(db, quotation, item, field_name, formula, input_data, process_text, result_value, operator):
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=item.id,
        calc_type="conductor",
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
