import json
import logging
import re
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
请面向审价人员表达，不要输出数据库 ID、英文属性名或 JSON 字段名。
材料行请按“制程 + 规格 + 中文字段名 + 当前值”的方式描述，例如：
“外被 万马105℃ TPU WMU-1189-Y OD:4.9mm 的 物料编码 为‘新开发’，系统无法匹配到对应的胶料单价。”

请输出简洁中文，包含：
1. 可能原因
2. 建议处理步骤
3. 是否需要新增/调整 Skill 或基础数据

重要约束：
- 如果材料行的“系统识别”显示“导体线径可解析”，不得诊断为“无法解析导体线径”。
- 如果材料行的“系统识别”显示“PVC/C类料号可识别”，不得诊断为“无法识别该 C 类料号”，只能判断是否缺少 BOM 售价或外购价格。
- 单价为空不等于解析失败；一键计算失败后事务会回滚，数据库里可能仍显示计算前的空单价。

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
            "行号": item.seq_no,
            "制程": item.process_name or "",
            "规格": item.spec_detail or "",
            "物料编码": item.process_code or "",
            "单位用量": _decimal_text(item.unit_usage),
            "单价": _decimal_text(item.unit_price),
            "材料金额": _decimal_text(item.material_amount),
            "行描述": _material_label(item),
            "系统识别": _recognition_text(item),
        }
        for item in sorted(
            [row for row in quotation.materials if not row.deleted],
            key=lambda row: (row.seq_no or 0, row.id or 0),
        )
    ]
    processes = [
        {
            "制程": item.process_name or "",
            "固定费用": _decimal_text(item.fixed_fee),
            "开机损耗废线": _decimal_text(item.startup_loss_wire),
            "每个制程总废胶": _decimal_text(item.total_waste_glue),
            "金额": _decimal_text(item.amount),
            "费用成本小计": _decimal_text(item.subtotal_fee),
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
            "计算类型": _calc_type_label(row.calc_type),
            "字段": _field_label(row.field_name),
            "formula": row.formula,
            "结果": _decimal_text(row.result_value),
            "计算过程": _clean_technical_text(row.process_text or "")[:500],
        }
        for row in traces
    ]

    return {
        "当前错误": _clean_technical_text(error_message or ""),
        "审价状态": "已报价" if get_review_status(quotation) == "quoted" else "待报价",
        "报价单": {
            "成本分析号": quotation.quotation_code or "",
            "客户名称": quotation.customer_name or "",
            "品名规格": quotation.product_spec or "",
            "结构": quotation.structure or "",
            "订单米数": _decimal_text(quotation.order_meterage),
            "材料成本": _decimal_text(quotation.material_cost),
            "费用总计": _decimal_text(quotation.total_fee),
            "最终售价": _decimal_text(quotation.final_selling_price),
        },
        "材料行": materials,
        "制程费用行": processes,
        "最近计算过程": trace_items,
    }


def _build_rule_summary(context: dict) -> str:
    error = context.get("当前错误") or context.get("error_message") or ""
    materials = context.get("材料行") or context.get("materials") or []
    processes = context.get("制程费用行") or context.get("processes") or []
    lines = []

    if error:
        lines.append(f"当前错误：{_clean_technical_text(error)}")

    if "未完成本次计算" in error:
        lines.append("平台已阻断最终售价，说明存在材料金额或制程费用小计不是本轮计算产生的，旧模板残留值不会参与汇总。")
        lines.append("优先检查错误中列出的材料/制程是否缺少 Skill 公式、缺少价格来源，或制程名称与上半部分材料名称未建立匹配关系。")
    if error and "失败：" in error:
        lines.append("一键计算是事务链：前面阶段即使已算出结果，只要后续阶段失败也会整体回滚。因此诊断应以“当前错误”里的失败阶段和缺失价格来源为准，不能仅凭页面单价为空判断前面阶段未解析。")
    if "未在 PVC 母料 BOM" in error or "v_qs_bzcb" in error:
        lines.append("存在材料未命中 PVC BOM 或外购价格视图。处理方式：维护基础价格/BOM，或在单价格子手填单价并保存后重算。")
    if "铜加工费" in error or "BC/TC" in error:
        lines.append("存在导体/编织取价问题。检查物料编码或规格中是否包含正确的 BC/TC 线径，或检查铜加工费基础数据。")
    if "订单米数" in error:
        lines.append("最终售价公式需要订单米数大于 0，请检查底部订单米数字段。")

    no_code_materials = [
        item for item in materials
        if item.get("制程") and not item.get("物料编码") and not item.get("单价")
    ]
    if no_code_materials:
        sample = "、".join(_material_text(item) for item in no_code_materials[:5])
        lines.append(f"发现部分材料行没有物料编码且没有有效单价：{sample}。这类行通常需要补料号、维护价格或手填单价。")

    unmatched_materials = [
        item for item in materials
        if item.get("制程") and item.get("物料编码") and not item.get("单价")
        and not _looks_like_conductor_material(item)
    ]
    if unmatched_materials:
        for item in unmatched_materials[:5]:
            lines.append(
                f"{_material_text(item)} 的 物料编码 为“{item.get('物料编码')}”，系统无法匹配到对应的{_price_label(item)}。"
                "请确认该物料编码是否需要维护基础价格/BOM，或由审价人员手填单价后保存再计算。"
            )

    process_names = {item.get("制程") for item in processes if item.get("制程")}
    known_keywords = ("铜", "导体", "编织", "绝缘", "芯押", "外被", "护套", "外护", "包带", "倒线", "集合")
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


def _material_label(item) -> str:
    parts = [str(item.process_name or "").strip(), str(item.spec_detail or "").strip()]
    return " ".join(part for part in parts if part) or f"第 {item.seq_no or '-'} 行材料"


def _material_text(item: dict) -> str:
    parts = [str(item.get("制程") or "").strip(), str(item.get("规格") or "").strip()]
    return " ".join(part for part in parts if part) or f"第 {item.get('行号') or '-'} 行材料"


def _recognition_text(item) -> str:
    text = f"{item.process_code or ''} {item.spec_detail or ''}".upper()
    conductor = re.search(r"\d+(?:\.\d+)?\s*(BC|TC)", text)
    c_codes = re.findall(r"\b(C[A-Z0-9*]{3,})\b", text)
    parts = []
    if conductor:
        parts.append(f"导体线径可解析：{conductor.group(0).replace(' ', '').upper()}")
    if c_codes:
        parts.append("PVC/C类料号可识别：" + "、".join(dict.fromkeys(c_codes)))
    return "；".join(parts) or "未识别到导体线径或 C 类料号"


def _looks_like_conductor_material(item: dict) -> bool:
    text = f"{item.get('制程') or ''} {item.get('规格') or ''} {item.get('物料编码') or ''}".upper()
    return any(keyword in text for keyword in ("铜", "导体", "编织")) or bool(re.search(r"\d+(?:\.\d+)?\s*(BC|TC)", text))


def _price_label(item: dict) -> str:
    text = f"{item.get('制程') or ''} {item.get('规格') or ''} {item.get('物料编码') or ''}".upper()
    if "色母" in text or "SPC" in text:
        return "色母单价"
    if any(keyword in text for keyword in ("PVC", "芯押", "绝缘", "外被", "护套", "TPU", "XLPE")):
        return "胶料单价"
    return "材料单价"


FIELD_LABELS = {
    "process_code": "物料编码",
    "unit_price": "单价",
    "material_amount": "材料金额",
    "process_amount": "金额",
    "process_subtotal_fee": "费用成本小计",
    "cost": "成本",
    "profit_selling_price": "取利售价",
    "non_profit_price": "不取利售价",
    "final_selling_price": "最终售价",
}

CALC_TYPE_LABELS = {
    "conductor": "导体/编织",
    "glue": "胶料",
    "external_material": "外购材料",
    "manual_material": "手填单价",
    "insulation": "绝缘制程",
    "jacket": "外被制程",
    "package_tape": "包带制程",
    "rewind": "倒线制程",
    "collection": "集合制程",
    "price_summary": "售价汇总",
}


def _field_label(value: str | None) -> str:
    return FIELD_LABELS.get(value or "", value or "")


def _calc_type_label(value: str | None) -> str:
    return CALC_TYPE_LABELS.get(value or "", value or "")


def _clean_technical_text(text: str) -> str:
    result = text or ""
    result = result.replace("process_code", "物料编码")
    result = result.replace("unit_price", "单价")
    result = result.replace("material_amount", "材料金额")
    result = result.replace("process_name", "制程")
    result = result.replace("spec_detail", "规格")
    result = result.replace("process_amount", "金额")
    result = result.replace("process_subtotal_fee", "费用成本小计")
    result = result.replace("`", "")
    result = result.replace("null", "空")
    result = result.replace("None", "空")
    result = re.sub(r"\s*\(ID\s*\d+\)", "", result)
    result = re.sub(r"\bID\s*\d+\b", "", result)
    result = re.sub(r"\s+", " ", result)
    return result.strip()
