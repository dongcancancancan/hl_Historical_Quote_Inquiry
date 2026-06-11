import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.calc_param import QuotationCalcParam
from app.models.copper_fee import CopperProcessingFee
from app.models.quotation import QuotationMain, QuotationMaterial, QuotationProcessFee
from app.services.calculation_context import CalculationContext
from app.services.copper_fee_service import create_copper_fee, update_copper_fee
from app.services.excel_preview_service import get_review_status, REVIEW_QUOTED
from app.services.unit_price_override_service import apply_unit_price_overrides, has_unit_price_override, load_unit_price_overrides


COPPER_CODE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(BC|TC|TD)", re.IGNORECASE)


def calculate_conductor_materials(
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

    conductor_rows = [
        item for item in quotation.materials
        if not item.deleted and _is_conductor_row(item)
    ]
    if not conductor_rows:
        raise ValueError("未找到导体/编织类制程行")

    platform_price_rows = [item for item in conductor_rows if not has_unit_price_override(item, unit_price_overrides)]
    params = (
        db.query(QuotationCalcParam)
        .filter(QuotationCalcParam.quotation_main_id == quotation.id)
        .first()
    )
    if platform_price_rows and (not params or params.copper_price is None):
        raise ValueError("请先填写并保存铜价，或为导体/编织行手填单价")

    calculated = 0
    process_calculated = 0
    skipped = []
    used_process_ids: set[int] = set()
    now = datetime.now()
    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "conductor",
        QuotationCalculationTrace.run_id.is_(None),
    ).delete(synchronize_session=False)

    for item in conductor_rows:
        is_manual_unit_price = has_unit_price_override(item, unit_price_overrides)
        parsed = None
        fee = None
        copper_price = None
        rod_fee = None
        vat_rate = None
        wire_fee = None
        backfilled_process_code = None

        if is_manual_unit_price:
            unit_price = Decimal(item.unit_price or 0)
            if unit_price <= 0:
                skipped.append({"id": item.id, "reason": f"{item.process_name or '导体/编织'}已设置手工单价但单价为空或无效"})
                continue
        else:
            parsed = _parse_copper_code(item)
            if not parsed:
                skipped.append({"id": item.id, "reason": "未从物料编码或规格中解析到 BC/TC/TD 线径，可手填单价后重新计算"})
                continue
            fee = _match_copper_fee(db, parsed["copper_type"], parsed["diameter"], operator=operator, auto_create=True)
            if not fee:
                skipped.append({"id": item.id, "reason": f"铜加工费未维护：{parsed['diameter']}{parsed['copper_type']}，可手填单价后重新计算"})
                continue

            copper_price = Decimal(params.copper_price)
            rod_fee = Decimal(params.copper_rod_process_fee)
            vat_rate = Decimal(params.vat_rate)
            wire_fee = Decimal(fee.processing_fee)
            unit_price = _round4((copper_price + rod_fee) / Decimal("1000") / vat_rate + wire_fee)
            backfilled_process_code = _backfill_copper_process_code(item, parsed)
        material_amount = _round4(Decimal(item.unit_usage or 0) * unit_price)

        if not is_manual_unit_price:
            item.unit_price = unit_price
        item.material_amount = material_amount
        item.updater = operator
        item.update_time = now
        if ctx:
            ctx.mark_material(item.id, "manual_unit_price" if is_manual_unit_price else "conductor")
        calculated += 1

        input_data = {
            "material_id": item.id,
            "process_name": item.process_name,
            "spec_detail": item.spec_detail,
            "process_code": item.process_code,
            "parsed_source_field": parsed["source_field"] if parsed else None,
            "backfilled_process_code": backfilled_process_code,
            "parsed_diameter": str(parsed["diameter"]) if parsed else None,
            "parsed_copper_type": parsed["copper_type"] if parsed else None,
            "matched_diameter": str(fee.diameter) if fee else None,
            "matched_copper_type": fee.copper_type if fee else None,
            "copper_price": str(copper_price) if copper_price is not None else None,
            "copper_rod_process_fee": str(rod_fee) if rod_fee is not None else None,
            "vat_rate": str(vat_rate) if vat_rate is not None else None,
            "wire_processing_fee": str(wire_fee) if wire_fee is not None else None,
            "auto_created_fee": bool(getattr(fee, "_auto_created_from_nearest", False)) if fee else False,
            "nearest_source_diameter": str(getattr(fee, "_source_diameter", "")) if fee and getattr(fee, "_auto_created_from_nearest", False) else None,
            "nearest_source_processing_fee": str(getattr(fee, "_source_processing_fee", "")) if fee and getattr(fee, "_auto_created_from_nearest", False) else None,
            "unit_usage": str(item.unit_usage or 0),
            "unit_price_source": "手工单价" if is_manual_unit_price else "平台计算",
        }
        if is_manual_unit_price:
            formula = "导体/编织单价 = 审价人员手填单价"
            process_text = (
                f"使用审价人员手填单价 {unit_price}，不覆盖数据库原始单价。\n"
                f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
            )
        else:
            formula = "导体/编织单价 = (铜价 + 铜杆加工费) / 1000 / 增值税率 + 铜加工费"
            auto_fee_text = ""
            if getattr(fee, "_auto_created_from_nearest", False):
                auto_fee_text = (
                    f"原线径 {parsed['diameter']}{parsed['copper_type']} 未维护，"
                    f"自动复用最接近线径 {getattr(fee, '_source_diameter', '')}{parsed['copper_type']} "
                    f"的加工费 {getattr(fee, '_source_processing_fee', wire_fee)}，并已补写到铜加工费表。\n"
                )
            backfill_text = f"物料编码为空，已回填为 {backfilled_process_code}。\n" if backfilled_process_code else ""
            process_text = (
                f"{auto_fee_text}"
                f"{backfill_text}"
                f"从 {parsed['source_field']} 解析到 {parsed['diameter']}{parsed['copper_type']}；"
                f"铜加工费匹配 {fee.diameter}{fee.copper_type} = {wire_fee} 元/KG。\n"
                f"导体/编织单价 = ({copper_price} + {rod_fee}) / 1000 / {vat_rate} + {wire_fee} = {unit_price}\n"
                f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
            )
        _add_trace(db, quotation, item, "unit_price", formula, input_data, process_text, unit_price, operator)
        _add_trace(
            db,
            quotation,
            item,
            "material_amount",
            "材料金额 = BOM用量 × 导体/编织单价",
            input_data,
            process_text,
            material_amount,
            operator,
        )

        processes = _match_process_fee_rows(quotation, item, used_process_ids)
        if not processes:
            skipped.append({"id": item.id, "reason": f"未找到对应的制程费用行：{item.process_name or ''}"})
            continue

        for process in processes:
            used_process_ids.add(process.id)
            startup_loss_wire = Decimal(process.startup_loss_wire or 0)
            fixed_fee = Decimal(process.fixed_fee or 0)
            startup_times = Decimal(quotation.order_startup_times or 0)
            if _is_braiding_material(item) or _is_braiding_process(process):
                base = _braiding_process_material_base(quotation, item)
                process_base_amount = base["base_amount"]
                process_amount = _round4(startup_loss_wire * process_base_amount)
            else:
                base = None
                process_base_amount = material_amount
                process_amount = _round4(startup_loss_wire * process_base_amount)
            subtotal_fee = _round4(fixed_fee + process_amount * startup_times)

            process.amount = process_amount
            process.subtotal_fee = subtotal_fee
            process.updater = operator
            process.update_time = now
            if ctx:
                ctx.mark_process(process.id, "conductor")
            process_calculated += 1

            process_input = dict(input_data)
            process_input.update({
                "process_fee_id": process.id,
                "process_fee_name": process.process_name,
                "startup_loss_wire": str(startup_loss_wire),
                "fixed_fee": str(fixed_fee),
                "order_startup_times": str(startup_times),
                "material_amount": str(material_amount),
                "process_base_amount": str(process_base_amount),
            })
            if base:
                process_input.update({
                    "braiding_material_id": item.id,
                    "braiding_material_seq_no": item.seq_no,
                    "material_amount_sum": str(base["material_amount_sum"]),
                    "excluded_after_braiding_amount": str(base["excluded_after_amount"]),
                    "excluded_after_braiding_rows": base["excluded_rows"],
                })
                process_formula = "编织金额 = 开机损耗废线 × (材料金额总和 - 编织之后材料金额合计)"
                excluded_text = "；".join(base["excluded_rows"]) or "无"
                fee_process_text = (
                    f"匹配编织制程费用行：{process.process_name or item.process_name or ''}\n"
                    f"编织之后材料：{excluded_text}\n"
                    f"参与材料金额 = 材料金额总和 {base['material_amount_sum']} - 编织之后材料金额合计 {base['excluded_after_amount']} = {process_base_amount}\n"
                    f"金额 = 开机损耗废线 {startup_loss_wire} × 参与材料金额 {process_base_amount} = {process_amount}\n"
                    f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 订单开机次数 {startup_times} = {subtotal_fee}"
                )
            else:
                process_formula = "金额 = 开机损耗废线 × 材料金额"
                fee_process_text = (
                    f"匹配制程费用行：{process.process_name or item.process_name or ''}\n"
                    f"金额 = 开机损耗废线 {startup_loss_wire} × 材料金额 {material_amount} = {process_amount}\n"
                    f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 订单开机次数 {startup_times} = {subtotal_fee}"
                )
            _add_trace(
                db,
                quotation,
                item,
                "process_amount",
                process_formula,
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
        message = "；".join(item["reason"] for item in skipped) or "没有可计算的导体/编织行"
        raise ValueError(message)
    if skipped:
        if calculated > 0 or process_calculated > 0:
            _recalculate_material_summary(quotation, operator, now)
            _recalculate_process_summary(quotation, operator, now)
            db.flush()
        message = "；".join(item["reason"] for item in skipped)
        raise ValueError(message)

    _recalculate_material_summary(quotation, operator, now)
    _recalculate_process_summary(quotation, operator, now)
    if commit:
        db.commit()
    else:
        db.flush()
    return {"calculated": calculated, "process_calculated": process_calculated, "skipped": skipped}


def refresh_braiding_process_fees(
    db: Session,
    quotation: QuotationMain,
    operator: str,
    now: datetime | None = None,
    ctx: CalculationContext | None = None,
    replace_existing_traces: bool = False,
) -> int:
    """Recalculate braiding process fees after later material rows have been priced."""
    braiding_materials = [
        item for item in _sorted_materials(quotation)
        if _is_braiding_material(item)
    ]
    if not braiding_materials:
        return 0
    if replace_existing_traces:
        db.query(QuotationCalculationTrace).filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type == "conductor",
            QuotationCalculationTrace.run_id.is_(None),
            QuotationCalculationTrace.material_id.in_([item.id for item in braiding_materials if item.id]),
            QuotationCalculationTrace.field_name.in_(["process_amount", "process_subtotal_fee"]),
        ).delete(synchronize_session=False)

    calculated = 0
    used_process_ids: set[int] = set()
    now = now or datetime.now()
    for item in braiding_materials:
        for process in _match_process_fee_rows(quotation, item, used_process_ids):
            used_process_ids.add(process.id)
            base = _braiding_process_material_base(quotation, item)
            process_base_amount = base["base_amount"]
            startup_loss_wire = Decimal(process.startup_loss_wire or 0)
            fixed_fee = Decimal(process.fixed_fee or 0)
            startup_times = Decimal(quotation.order_startup_times or 0)
            process_amount = _round4(startup_loss_wire * process_base_amount)
            subtotal_fee = _round4(fixed_fee + process_amount * startup_times)

            process.amount = process_amount
            process.subtotal_fee = subtotal_fee
            process.updater = operator
            process.update_time = now
            if ctx:
                ctx.mark_process(process.id, "conductor")
            calculated += 1

            process_input = {
                "material_id": item.id,
                "process_name": item.process_name,
                "spec_detail": item.spec_detail,
                "process_code": item.process_code,
                "process_fee_id": process.id,
                "process_fee_name": process.process_name,
                "startup_loss_wire": str(startup_loss_wire),
                "fixed_fee": str(fixed_fee),
                "order_startup_times": str(startup_times),
                "material_amount": str(Decimal(item.material_amount or 0)),
                "process_base_amount": str(process_base_amount),
                "braiding_material_seq_no": item.seq_no,
                "material_amount_sum": str(base["material_amount_sum"]),
                "excluded_after_braiding_amount": str(base["excluded_after_amount"]),
                "excluded_after_braiding_rows": base["excluded_rows"],
                "refresh_source": "after_material_pricing",
            }
            excluded_text = "；".join(base["excluded_rows"]) or "无"
            fee_process_text = (
                f"刷新编织制程费用行：{process.process_name or item.process_name or ''}\n"
                f"编织之后材料：{excluded_text}\n"
                f"参与材料金额 = 材料金额总和 {base['material_amount_sum']} - 编织之后材料金额合计 {base['excluded_after_amount']} = {process_base_amount}\n"
                f"金额 = 开机损耗废线 {startup_loss_wire} × 参与材料金额 {process_base_amount} = {process_amount}\n"
                f"费用成本小计 = 固定费用 {fixed_fee} + 金额 {process_amount} × 订单开机次数 {startup_times} = {subtotal_fee}"
            )
            _add_trace(
                db,
                quotation,
                item,
                "process_amount",
                "编织金额 = 开机损耗废线 × (材料金额总和 - 编织之后材料金额合计)",
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
    return calculated


def list_conductor_traces(
    db: Session,
    quotation: QuotationMain,
    bpm_instance_id: int | None = None,
    run_id: int | None = None,
) -> list[dict]:
    query = db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "conductor",
    )
    if run_id:
        query = query.filter(QuotationCalculationTrace.run_id == run_id)
    elif bpm_instance_id:
        query = query.filter(QuotationCalculationTrace.bpm_instance_id == bpm_instance_id)
    rows = query.order_by(QuotationCalculationTrace.create_time.desc(), QuotationCalculationTrace.id.desc()).limit(200).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "bpm_instance_id": row.bpm_instance_id,
            "material_id": row.material_id,
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


def _is_conductor_row(item: QuotationMaterial) -> bool:
    text = f"{item.process_name or ''} {item.process_code or ''} {item.spec_detail or ''}".upper()
    process_name = item.process_name or ""
    if "芯绞" in process_name:
        return False
    if COPPER_CODE_RE.search(text):
        return True
    if _looks_like_external_material_code(item.process_code):
        return False
    return (
        "铜" in process_name
        or "导体" in process_name
        or "编织" in process_name
    )


def _match_process_fee_row(
    quotation: QuotationMain,
    material: QuotationMaterial,
    used_process_ids: set[int],
) -> QuotationProcessFee | None:
    rows = _match_process_fee_rows(quotation, material, used_process_ids)
    return rows[0] if rows else None


def _match_process_fee_rows(
    quotation: QuotationMain,
    material: QuotationMaterial,
    used_process_ids: set[int],
) -> list[QuotationProcessFee]:
    processes = [
        item for item in quotation.processes
        if not item.deleted and item.id not in used_process_ids
    ]
    processes.sort(key=lambda item: item.id or 0)
    material_name = _normalize_process_name(material.process_name)
    if material_name:
        exact = [
            process for process in processes
            if _normalize_process_name(process.process_name) == material_name
        ]
        if exact:
            return [exact[0]]
    if _is_braiding_material(material):
        return _first_process(processes, _is_braiding_process)
    if _is_copper_material(material):
        return _first_process(processes, _is_copper_process)
    return _first_process(processes, _is_conductor_process)


def _first_process(processes: list[QuotationProcessFee], predicate) -> list[QuotationProcessFee]:
    for process in processes:
        if predicate(process):
            return [process]
    return []


def _braiding_process_material_base(quotation: QuotationMain, braiding_material: QuotationMaterial) -> dict:
    materials = _sorted_materials(quotation)
    braiding_key = _material_sort_key(braiding_material)
    material_amount_sum = _round4(sum(Decimal(item.material_amount or 0) for item in materials))
    excluded_rows = [
        item for item in materials
        if _material_sort_key(item) > braiding_key
    ]
    excluded_after_amount = _round4(sum(Decimal(item.material_amount or 0) for item in excluded_rows))
    base_amount = _round4(material_amount_sum - excluded_after_amount)
    return {
        "material_amount_sum": material_amount_sum,
        "excluded_after_amount": excluded_after_amount,
        "base_amount": base_amount,
        "excluded_rows": [
            (
                f"{item.seq_no or '-'} {item.process_name or '材料'} "
                f"{item.spec_detail or item.process_code or ''}：{_decimal_text(item.material_amount)}"
            ).strip()
            for item in excluded_rows
        ],
    }


def _sorted_materials(quotation: QuotationMain) -> list[QuotationMaterial]:
    return sorted(
        [item for item in quotation.materials if not item.deleted],
        key=_material_sort_key,
    )


def _material_sort_key(item: QuotationMaterial) -> tuple[int, int]:
    seq = item.seq_no if item.seq_no is not None else 10**9
    return int(seq), int(item.id or 0)


def _looks_like_external_material_code(value) -> bool:
    raw_code = str(value or "").strip().upper()
    if not raw_code or raw_code in {"新开发", "NULL", "NONE", "-"}:
        return False
    return not bool(COPPER_CODE_RE.search(raw_code))


def _normalize_process_name(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _is_conductor_process(process: QuotationProcessFee) -> bool:
    name = process.process_name or ""
    return "铜" in name or "导体" in name or "编织" in name


def _is_braiding_material(material: QuotationMaterial) -> bool:
    return "编织" in str(material.process_name or "")


def _is_copper_material(material: QuotationMaterial) -> bool:
    name = str(material.process_name or "")
    text = f"{material.process_code or ''} {material.spec_detail or ''}"
    return "铜" in name or "导体" in name or bool(COPPER_CODE_RE.search(text))


def _is_braiding_process(process: QuotationProcessFee) -> bool:
    return "编织" in str(process.process_name or "")


def _is_copper_process(process: QuotationProcessFee) -> bool:
    name = str(process.process_name or "")
    return "编织" not in name and ("铜" in name or "导体" in name)


def _parse_copper_code(item: QuotationMaterial) -> dict | None:
    for field_name, value in (("物料编码", item.process_code), ("规格", item.spec_detail)):
        match = COPPER_CODE_RE.search(str(value or ""))
        if match:
            copper_type = match.group(2).upper()
            if copper_type == "TD":
                copper_type = "TC"  # TD 视为 TC
            return {
                "diameter": Decimal(match.group(1)),
                "copper_type": copper_type,
                "matched_code": f"{match.group(1)}{copper_type}",
                "source_field": field_name,
            }
    return None


def _backfill_copper_process_code(item: QuotationMaterial, parsed: dict | None) -> str | None:
    if not parsed or parsed.get("source_field") != "规格":
        return None
    current = str(item.process_code or "").strip()
    if current and current.upper() not in {"NULL", "NONE", "-", "新开发"}:
        return None
    code = parsed.get("matched_code") or _format_copper_code(parsed["diameter"], parsed["copper_type"])
    item.process_code = code
    return code


def _format_copper_code(diameter: Decimal, copper_type: str) -> str:
    diameter_text = f"{Decimal(diameter):f}".rstrip("0").rstrip(".") or "0"
    return f"{diameter_text}{str(copper_type or '').upper()}"


def _match_copper_fee(
    db: Session,
    copper_type: str,
    diameter: Decimal,
    operator: str | None = None,
    auto_create: bool = False,
):
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
        candidate_fee = Decimal(candidate.processing_fee or 0)
        nearest_fee = Decimal(nearest.processing_fee or 0) if nearest else Decimal("-1")
        if (
            nearest_diff is None
            or diff < nearest_diff
            or (diff == nearest_diff and candidate_fee > nearest_fee)
        ):
            nearest = candidate
            nearest_diff = diff
    if not nearest:
        return None
    if auto_create and operator:
        return _create_missing_copper_fee_from_nearest(db, copper_type, diameter, basis, nearest, operator)
    return nearest


def _create_missing_copper_fee_from_nearest(
    db: Session,
    copper_type: str,
    diameter: Decimal,
    basis: Decimal,
    nearest: CopperProcessingFee,
    operator: str,
) -> CopperProcessingFee:
    payload = {
        "copper_type": copper_type,
        "diameter": diameter,
        "tin_price_basis": basis,
        "processing_fee": nearest.processing_fee,
        "minimum_fee": nearest.minimum_fee,
        "remark": (
            f"自动复用最接近线径 {nearest.diameter}{nearest.copper_type} 的加工费；"
            f"源记录ID {nearest.id}"
        ),
        "enabled": True,
    }
    disabled_exact = db.query(CopperProcessingFee).filter(
        CopperProcessingFee.copper_type == copper_type,
        CopperProcessingFee.diameter == diameter,
        CopperProcessingFee.tin_price_basis == basis,
        CopperProcessingFee.enabled == False,
    ).first()
    if disabled_exact:
        fee = update_copper_fee(db, disabled_exact, payload, operator, action="AUTO_NEAREST", commit=False)
    else:
        fee = create_copper_fee(db, payload, operator, action="AUTO_NEAREST", commit=False)

    fee._auto_created_from_nearest = True
    fee._source_fee_id = nearest.id
    fee._source_diameter = nearest.diameter
    fee._source_processing_fee = nearest.processing_fee
    return fee


def _add_trace(db, quotation, item, field_name, formula, input_data, process_text, result_value, operator):
    entity_type = "main"
    entity_id = quotation.id
    material_id = None
    if item is not None:
        material_id = item.id
        if hasattr(item, "spec_detail"):
            entity_type = "material"
            entity_id = item.id
        elif hasattr(item, "startup_loss_wire"):
            entity_type = "process"
            entity_id = item.id
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=material_id,
        entity_type=entity_type,
        entity_id=entity_id,
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
