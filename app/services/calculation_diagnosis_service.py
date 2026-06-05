import json
import logging
from decimal import Decimal

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.calculation_trace import QuotationCalculationTrace
from app.models.quotation import QuotationMain
from app.services.calculation_skill_engine import list_calculation_skills
from app.services.excel_preview_service import get_review_status

logger = logging.getLogger(__name__)


DIAGNOSIS_PROMPT = """你是电缆成本分析平台的审价计算诊断助手。
你只能基于平台提供的事实诊断问题，不能编造数据库中不存在的价格、公式或金额。

请输出简洁中文，包含：
1. 可能原因
2. 建议处理步骤
3. 是否需要新增/调整 Skill 或基础数据

【报价单上下文】
{context_json}

【规则诊断】
{rule_summary}
"""


async def diagnose_calculation(db: Session, quotation: QuotationMain, error_message: str | None = None) -> dict:
    context = _build_diagnosis_context(db, quotation, error_message)
    rule_summary = _build_rule_summary(context)
    llm_text = ""
    llm_enabled = bool(settings.DEEPSEEK_API_KEY)

    if llm_enabled:
        try:
            client = AsyncOpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
            response = await client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是严谨的制造业报价计算诊断助手。"},
                    {
                        "role": "user",
                        "content": DIAGNOSIS_PROMPT.format(
                            context_json=json.dumps(context, ensure_ascii=False, indent=2),
                            rule_summary=rule_summary,
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=1200,
            )
            llm_text = (response.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("LLM calculation diagnosis failed: %s", exc)
            llm_text = ""

    return {
        "quotation_code": quotation.quotation_code or "",
        "mode": "llm" if llm_text else "rule",
        "summary": llm_text or rule_summary,
        "rule_summary": rule_summary,
        "skills": list_calculation_skills(),
        "context": context,
    }


def _build_diagnosis_context(db: Session, quotation: QuotationMain, error_message: str | None) -> dict:
    materials = [
        {
            "id": item.id,
            "seq_no": item.seq_no,
            "process_name": item.process_name or "",
            "spec_detail": item.spec_detail or "",
            "process_code": item.process_code or "",
            "unit_usage": _decimal_text(item.unit_usage),
            "unit_price": _decimal_text(item.unit_price),
            "material_amount": _decimal_text(item.material_amount),
        }
        for item in sorted(
            [row for row in quotation.materials if not row.deleted],
            key=lambda row: (row.seq_no or 0, row.id or 0),
        )
    ]
    processes = [
        {
            "id": item.id,
            "process_name": item.process_name or "",
            "fixed_fee": _decimal_text(item.fixed_fee),
            "startup_loss_wire": _decimal_text(item.startup_loss_wire),
            "total_waste_glue": _decimal_text(item.total_waste_glue),
            "amount": _decimal_text(item.amount),
            "subtotal_fee": _decimal_text(item.subtotal_fee),
        }
        for item in sorted(
            [row for row in quotation.processes if not row.deleted],
            key=lambda row: row.id or 0,
        )
    ]
    traces = (
        db.query(QuotationCalculationTrace)
        .filter(QuotationCalculationTrace.quotation_main_id == quotation.id)
        .order_by(QuotationCalculationTrace.create_time.desc(), QuotationCalculationTrace.id.desc())
        .limit(30)
        .all()
    )
    trace_items = [
        {
            "calc_type": row.calc_type,
            "field_name": row.field_name,
            "formula": row.formula,
            "result_value": _decimal_text(row.result_value),
            "process_text": (row.process_text or "")[:500],
            "create_time": row.create_time.isoformat() if row.create_time else None,
        }
        for row in traces
    ]

    return {
        "error_message": error_message or "",
        "review_status": get_review_status(quotation),
        "quotation": {
            "id": quotation.id,
            "quotation_code": quotation.quotation_code or "",
            "customer_name": quotation.customer_name or "",
            "product_spec": quotation.product_spec or "",
            "structure": quotation.structure or "",
            "order_meterage": _decimal_text(quotation.order_meterage),
            "material_cost": _decimal_text(quotation.material_cost),
            "total_fee": _decimal_text(quotation.total_fee),
            "final_selling_price": _decimal_text(quotation.final_selling_price),
        },
        "materials": materials,
        "processes": processes,
        "recent_traces": trace_items,
    }


def _build_rule_summary(context: dict) -> str:
    error = context.get("error_message") or ""
    materials = context.get("materials") or []
    processes = context.get("processes") or []
    lines = []

    if error:
        lines.append(f"当前错误：{error}")

    if "未完成本次计算" in error:
        lines.append("平台已阻断最终售价，说明存在材料金额或制程费用小计不是本轮计算产生的，旧模板残留值不会参与汇总。")
        lines.append("优先检查错误中列出的材料/制程是否缺少 Skill 公式、缺少价格来源，或制程名称与上半部分材料名称未建立匹配关系。")
    if "未在 PVC 母料 BOM" in error or "v_qs_bzcb" in error:
        lines.append("存在材料未命中 PVC BOM 或外购价格视图。处理方式：维护基础价格/BOM，或在单价格子手填单价并保存后重算。")
    if "铜加工费" in error or "BC/TC" in error:
        lines.append("存在导体/编织取价问题。检查物料编码或规格中是否包含正确的 BC/TC 线径，或检查铜加工费基础数据。")
    if "订单米数" in error:
        lines.append("最终售价公式需要订单米数大于 0，请检查底部订单米数字段。")

    no_code_materials = [
        item for item in materials
        if item.get("process_name") and not item.get("process_code") and not item.get("unit_price")
    ]
    if no_code_materials:
        sample = "、".join(f"{item.get('seq_no')}.{item.get('process_name')}" for item in no_code_materials[:5])
        lines.append(f"发现部分材料行没有物料编码且没有有效单价：{sample}。这类行通常需要补料号、维护价格或手填单价。")

    process_names = {item.get("process_name") for item in processes if item.get("process_name")}
    known_keywords = ("铜", "导体", "编织", "绝缘", "芯押", "外被", "护套", "倒线", "集合")
    unknown_processes = [name for name in process_names if not any(key in name for key in known_keywords)]
    if unknown_processes:
        lines.append("发现可能尚未 Skill 化的制程费用行：" + "、".join(sorted(unknown_processes)[:8]) + "。需要确认公式后新增 Skill 或匹配规则。")

    if not lines:
        lines.append("未发现明显规则异常。建议查看导体/胶料/售价计算过程，确认本次计算是否已覆盖所有材料行和制程费用行。")

    lines.append("当前已注册 Skill：" + "、".join(skill["name"] for skill in list_calculation_skills()))
    return "\n".join(lines)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
