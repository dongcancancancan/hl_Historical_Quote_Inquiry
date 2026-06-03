import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationTrace
from app.models.quotation import QuotationMain, QuotationMaterial
from app.services.excel_preview_service import REVIEW_QUOTED, get_review_status


PVC_CODE_RE = re.compile(r"\b(C[A-Z0-9*]{3,})\b", re.IGNORECASE)


def calculate_glue_materials(db: Session, quotation: QuotationMain, operator: str) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能重新计算")

    candidates = [item for item in quotation.materials if not item.deleted and _has_c_code(item)]
    if not candidates:
        raise ValueError("未找到 C 开头胶料制程行")

    calculated = 0
    skipped = []
    now = datetime.now()
    db.query(QuotationCalculationTrace).filter(
        QuotationCalculationTrace.quotation_main_id == quotation.id,
        QuotationCalculationTrace.calc_type == "glue",
    ).delete(synchronize_session=False)

    for item in candidates:
        lookup = _resolve_pvc_bom(db, item)
        if not lookup:
            skipped.append({"id": item.id, "reason": f"未找到 PVC 母料 BOM：{item.process_code or item.spec_detail or ''}"})
            continue

        unit_price = _round4(lookup["saleprice"])
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
            "resolved_bom_no": lookup["bom_no"],
            "source_text": lookup["source_text"],
            "saleprice": str(lookup["saleprice"]),
            "unit_usage": str(item.unit_usage or 0),
        }
        process_text = (
            f"从 {lookup['source_text']} 解析并匹配到 PVC 母料 {lookup['bom_no']}；"
            f"该母料售价 = {lookup['saleprice']}。\n"
            f"胶料单价 = PVC 母料售价 = {unit_price}\n"
            f"材料金额 = BOM用量 {item.unit_usage or 0} × 单价 {unit_price} = {material_amount}"
        )
        _add_trace(
            db,
            quotation,
            item,
            "unit_price",
            "胶料单价 = PVC 母料 BOM 售价",
            input_data,
            process_text,
            unit_price,
            operator,
        )
        _add_trace(
            db,
            quotation,
            item,
            "material_amount",
            "材料金额 = BOM用量 × 胶料单价",
            input_data,
            process_text,
            material_amount,
            operator,
        )

    if calculated == 0:
        message = "；".join(item["reason"] for item in skipped) or "没有可计算的胶料行"
        raise ValueError(message)

    _recalculate_material_summary(quotation, operator, now)
    db.commit()
    return {"calculated": calculated, "skipped": skipped}


def list_glue_traces(db: Session, quotation: QuotationMain) -> list[dict]:
    rows = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.calc_type == "glue",
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


def _add_trace(db, quotation, item, field_name, formula, input_data, process_text, result_value, operator):
    db.add(QuotationCalculationTrace(
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        material_id=item.id,
        calc_type="glue",
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


def _round4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
