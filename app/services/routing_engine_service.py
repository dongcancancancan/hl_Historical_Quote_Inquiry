from __future__ import annotations

import asyncio
import json
import logging
import re
from decimal import Decimal

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.services.calculation_skill_engine import list_calculation_skills
from app.services.routing_decision_service import (
    DEFAULT_DECISION_JSON,
    create_routing_decision_run,
)
from app.services.routing_policy_service import (
    DEFAULT_ROUTE_SCOPE,
    get_active_policy,
    parse_prompt_rules,
)

logger = logging.getLogger(__name__)

ROUTING_SYSTEM_PROMPT = """你是电缆成本分析平台的路由决策助手。
你只负责判断应该调用哪个 skill，以及哪些材料行和制程费用行相关；你绝对不能进行金额计算，不能编造公式，不能输出价格、金额、成本或任何额外字段。

你必须遵守以下约束：
1. 只能从给定的候选 skill 中选择 target_skill。
2. 只能使用输入中真实存在的材料行 ID 和制程行 ID。
3. 如果无法可靠判断，必须返回 manual_review_required=true。
4. 只输出 JSON，对象字段必须严格符合指定 schema，不能增加任何额外字段。
"""

ROUTING_USER_PROMPT = """请根据当前成本分析表上下文，做一次“只决策、不计算”的 skill 路由判断。

【路由场景】
{route_scene}

【触发来源】
{trigger_source}

【失败上下文】
{error_message}

【重点材料行】
{focus_material_ids}

【重点制程行】
{focus_process_ids}

【可用候选 Skill】
{candidate_skills_json}

【策略规则】
{policy_rules_json}

【当前报价单上下文】
{input_snapshot_json}

【输出 JSON Schema】
{{
  "route_type": "route_skill | manual_review | reject",
  "target_skill": "必须来自候选 skill；如果不是 route_skill 可为空字符串",
  "target_subtype": "可为空字符串",
  "mapping_mode": "one_to_one | one_to_many | many_to_one | ambiguous | none",
  "confidence": 0.0,
  "reason": "中文简述原因",
  "matched_material_ids": [1, 2],
  "matched_process_ids": [8],
  "manual_review_required": false
}}

注意：
- 不允许输出 schema 之外的字段。
- 不允许输出公式、金额、价格、成本、单价、材料金额、费用金额等内容。
- 如果没有足够把握，请把 route_type 设为 manual_review，或保持 manual_review_required=true。
"""

ROUTE_PLAN_SYSTEM_PROMPT = """你是电缆成本分析平台的 route_plan 编排助手。
你只负责把成本分析表拆成若干个“材料/制程/skill”分组，并指出未匹配项；你绝对不能进行金额计算，不能编造公式，不能输出价格、金额、成本或任何额外字段。

你必须遵守以下约束：
1. 只能从给定候选 skill 中为每个 group 选择 target_skill。
2. 只能使用输入中真实存在的材料行 ID 和制程行 ID。
3. 每个 group 要表达“这一组由哪个 skill 处理”，而不是只返回一个主推荐 skill。
4. 如果存在歧义、价格缺失、上下配对不唯一，必须保留 manual_review_required=true。
5. 只输出 JSON，对象字段必须严格符合指定 schema，不能增加任何额外字段。
"""

ROUTE_PLAN_USER_PROMPT = """请根据当前成本分析表上下文，生成一次“只做编排、不做计算”的 route_plan。

【路由场景】
{route_scene}

【触发来源】
{trigger_source}

【失败上下文】
{error_message}

【重点材料行】
{focus_material_ids}

【重点制程行】
{focus_process_ids}

【可用候选 Skill】
{candidate_skills_json}

【策略规则】
{policy_rules_json}

【当前报价单上下文】
{input_snapshot_json}

【输出 JSON Schema】
{{
  "route_type": "route_plan",
  "summary_status": "full_match | partial_match | manual_review_only | reject",
  "manual_review_required": true,
  "confidence": 0.0,
  "reason": "中文简述总体原因",
  "quotation_code": "当前成本分析号",
  "instance_id": 0,
  "groups": [
    {{
      "group_id": "grp_1",
      "step_order": 1,
      "group_type": "conductor_stage | glue_stage | price_summary_stage | mixed_stage | unknown_stage",
      "target_skill": "必须来自候选 skill",
      "match_status": "matched | partially_matched | ambiguous | unmatched",
      "manual_review_required": false,
      "confidence": 0.0,
      "material_ids": [1, 2],
      "process_ids": [8, 9],
      "material_names": ["铜绞"],
      "process_names": ["铜绞"],
      "reason": "该组为何由该 skill 处理",
      "rule_hits": ["same_process_order_match"]
    }}
  ],
  "unmatched_material_ids": [3],
  "unmatched_process_ids": [10],
  "unmatched_details": [
    {{
      "item_type": "material | process",
      "item_id": 3,
      "item_name": "芯押",
      "status": "unmatched",
      "suggested_skill": "glue_external_and_process",
      "manual_review_required": true,
      "reason": "为什么未匹配成功"
    }}
  ],
  "warnings": ["价格缺失不是路由问题"],
  "meta": {{
    "candidate_skills": ["conductor_material_and_process"],
    "rule_hits": ["same_process_order_match"]
  }}
}}

注意：
- 不允许输出 schema 之外的字段。
- 不允许输出公式、金额、价格、成本、单价、材料金额、费用金额等内容。
- 如果一张表需要多个阶段，请在 groups 中分别给出每组，不要只选一个 skill。
- 如果是价格缺失，应在 reason 或 warnings 中明确写出“价格缺失不是路由问题”。
- 如果无法可靠形成 route_plan，请返回 summary_status=manual_review_only 或 reject。
"""

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
_async_client = AsyncOpenAI(
    api_key=settings.DEEPSEEK_API_KEY,
    base_url=settings.DEEPSEEK_BASE_URL,
)


async def route_calculation_skill(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
    route_scene: str,
    trigger_source: str,
    error_message: str | None = None,
    focus_material_ids: list[int] | None = None,
    focus_process_ids: list[int] | None = None,
) -> dict:
    candidate_skills = _candidate_skills()
    candidate_skill_ids = [item["id"] for item in candidate_skills]
    input_snapshot = _build_input_snapshot(
        quotation,
        instance,
        route_scene,
        trigger_source,
        error_message=error_message,
        focus_material_ids=focus_material_ids,
        focus_process_ids=focus_process_ids,
    )
    tenant_id = (quotation.tenant_id or "").strip()

    if not tenant_id:
        return _write_non_llm_decision(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skill_ids=candidate_skill_ids,
            final_action="reject",
            reason="当前报价单缺少 tenant_id，无法匹配路由策略",
        )

    policy = get_active_policy(db, tenant_id, DEFAULT_ROUTE_SCOPE)
    if not policy:
        return _write_non_llm_decision(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skill_ids=candidate_skill_ids,
            final_action="reject",
            reason="未找到已发布且启用的路由策略",
        )

    if not settings.DEEPSEEK_API_KEY:
        return _write_non_llm_decision(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skill_ids=candidate_skill_ids,
            policy=policy,
            llm_model=policy.llm_model or settings.DEEPSEEK_MODEL,
            final_action="reject",
            reason="未配置 LLM API Key，无法执行路由测试",
        )

    prompt = _build_prompt(policy.prompt_rules, candidate_skills, input_snapshot, route_scene, trigger_source)
    llm_response_text = ""

    try:
        response = await _async_client.chat.completions.create(
            model=policy.llm_model or settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1200,
        )
        llm_response_text = (response.choices[0].message.content or "").strip()
        if not llm_response_text:
            raise ValueError("LLM 返回为空")

        raw_decision = _parse_json_object(llm_response_text)
        validated = _validate_decision(
            raw_decision=raw_decision,
            quotation=quotation,
            candidate_skill_ids=candidate_skill_ids,
            confidence_threshold=policy.confidence_threshold,
        )
        run = create_routing_decision_run(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skills=candidate_skill_ids,
            final_action=validated["final_action"],
            policy=policy,
            llm_model=policy.llm_model or settings.DEEPSEEK_MODEL,
            llm_prompt_text=prompt,
            llm_response_text=llm_response_text,
            decision_json=validated["decision"],
            confidence=validated["decision"]["confidence"],
            final_skill=validated["decision"].get("target_skill") or None,
            adopt_status="pending",
            error_message=validated["error_message"],
        )
        return {
            "routing_run_id": run.id,
            "policy_id": policy.id,
            "policy_name": policy.policy_name,
            "decision": validated["decision"],
            "final_action": validated["final_action"],
            "final_skill": run.final_skill or "",
            "adopt_status": run.adopt_status,
            "error_message": validated["error_message"],
        }
    except Exception as exc:
        logger.warning(
            "LLM routing dry-run failed for quotation %s: %s; raw_response=%s",
            quotation.quotation_code,
            exc,
            (llm_response_text or "")[:1000],
        )
        decision = dict(DEFAULT_DECISION_JSON)
        decision.update({
            "route_type": "reject",
            "reason": str(exc),
            "manual_review_required": True,
        })
        run = create_routing_decision_run(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skills=candidate_skill_ids,
            final_action="reject",
            policy=policy,
            llm_model=policy.llm_model or settings.DEEPSEEK_MODEL,
            llm_prompt_text=prompt,
            llm_response_text=llm_response_text,
            decision_json=decision,
            confidence=0,
            final_skill=None,
            adopt_status="pending",
            error_message=str(exc),
        )
        return {
            "routing_run_id": run.id,
            "policy_id": policy.id,
            "policy_name": policy.policy_name,
            "decision": decision,
            "final_action": "reject",
            "final_skill": "",
            "adopt_status": run.adopt_status,
            "error_message": str(exc),
        }


def run_routing_dry_run_sync(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
    route_scene: str,
    trigger_source: str,
    error_message: str | None = None,
    focus_material_ids: list[int] | None = None,
    focus_process_ids: list[int] | None = None,
) -> dict:
    return asyncio.run(
        route_calculation_skill(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            error_message=error_message,
            focus_material_ids=focus_material_ids,
            focus_process_ids=focus_process_ids,
        )
    )


async def route_calculation_plan(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
    route_scene: str,
    trigger_source: str,
    error_message: str | None = None,
    focus_material_ids: list[int] | None = None,
    focus_process_ids: list[int] | None = None,
) -> dict:
    candidate_skills = _candidate_skills()
    candidate_skill_ids = [item["id"] for item in candidate_skills]
    input_snapshot = _build_input_snapshot(
        quotation,
        instance,
        route_scene,
        trigger_source,
        error_message=error_message,
        focus_material_ids=focus_material_ids,
        focus_process_ids=focus_process_ids,
    )
    tenant_id = (quotation.tenant_id or "").strip()

    if not tenant_id:
        return _write_non_llm_route_plan(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skill_ids=candidate_skill_ids,
            final_action="reject",
            reason="当前报价单缺少 tenant_id，无法匹配路由策略",
        )

    policy = get_active_policy(db, tenant_id, DEFAULT_ROUTE_SCOPE)
    if not policy:
        return _write_non_llm_route_plan(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skill_ids=candidate_skill_ids,
            final_action="reject",
            reason="未找到已发布且启用的路由策略",
        )

    if not settings.DEEPSEEK_API_KEY:
        return _write_non_llm_route_plan(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skill_ids=candidate_skill_ids,
            policy=policy,
            llm_model=policy.llm_model or settings.DEEPSEEK_MODEL,
            final_action="reject",
            reason="未配置 LLM API Key，无法执行 route_plan 测试",
        )

    prompt = _build_route_plan_prompt(policy.prompt_rules, candidate_skills, input_snapshot, route_scene, trigger_source)
    llm_response_text = ""

    try:
        response = await _async_client.chat.completions.create(
            model=policy.llm_model or settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": ROUTE_PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1800,
        )
        llm_response_text = (response.choices[0].message.content or "").strip()
        if not llm_response_text:
            raise ValueError("LLM 返回为空")

        raw_plan = _parse_json_object(llm_response_text)
        validated = _validate_route_plan(
            raw_plan=raw_plan,
            quotation=quotation,
            instance=instance,
            candidate_skill_ids=candidate_skill_ids,
            confidence_threshold=policy.confidence_threshold,
        )
        run = create_routing_decision_run(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skills=candidate_skill_ids,
            final_action=validated["final_action"],
            policy=policy,
            llm_model=policy.llm_model or settings.DEEPSEEK_MODEL,
            llm_prompt_text=prompt,
            llm_response_text=llm_response_text,
            decision_json=validated["plan"],
            confidence=validated["plan"]["confidence"],
            final_skill="route_plan",
            adopt_status="pending",
            error_message=validated["error_message"],
        )
        return {
            "routing_run_id": run.id,
            "policy_id": policy.id,
            "policy_name": policy.policy_name,
            "route_plan": validated["plan"],
            "final_action": validated["final_action"],
            "final_skill": run.final_skill or "",
            "adopt_status": run.adopt_status,
            "error_message": validated["error_message"],
        }
    except Exception as exc:
        logger.warning(
            "LLM route-plan dry-run failed for quotation %s: %s; raw_response=%s",
            quotation.quotation_code,
            exc,
            (llm_response_text or "")[:1000],
        )
        plan = _default_route_plan(quotation, instance, str(exc), summary_status="reject")
        run = create_routing_decision_run(
            db=db,
            quotation=quotation,
            instance=instance,
            operator=operator,
            route_scene=route_scene,
            trigger_source=trigger_source,
            input_snapshot=input_snapshot,
            candidate_skills=candidate_skill_ids,
            final_action="reject",
            policy=policy,
            llm_model=policy.llm_model or settings.DEEPSEEK_MODEL,
            llm_prompt_text=prompt,
            llm_response_text=llm_response_text,
            decision_json=plan,
            confidence=0,
            final_skill="route_plan",
            adopt_status="pending",
            error_message=str(exc),
        )
        return {
            "routing_run_id": run.id,
            "policy_id": policy.id,
            "policy_name": policy.policy_name,
            "route_plan": plan,
            "final_action": "reject",
            "final_skill": "route_plan",
            "adopt_status": run.adopt_status,
            "error_message": str(exc),
        }


def _candidate_skills() -> list[dict]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "phase": item["phase"],
            "description": item["description"],
            "capabilities": item["capabilities"],
        }
        for item in list_calculation_skills()
    ]


def _build_input_snapshot(
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    route_scene: str,
    trigger_source: str,
    error_message: str | None = None,
    focus_material_ids: list[int] | None = None,
    focus_process_ids: list[int] | None = None,
) -> dict:
    materials = sorted(
        [item for item in quotation.materials if not item.deleted],
        key=lambda item: (item.seq_no or 0, item.id or 0),
    )
    processes = sorted(
        [item for item in quotation.processes if not item.deleted],
        key=lambda item: item.id or 0,
    )
    material_ids = {item.id for item in materials if item.id is not None}
    process_ids = {item.id for item in processes if item.id is not None}
    focus_materials = _normalize_focus_ids(focus_material_ids, material_ids, "focus_material_ids")
    focus_processes = _normalize_focus_ids(focus_process_ids, process_ids, "focus_process_ids")
    return {
        "meta": {
            "quotation_main_id": quotation.id,
            "bpm_instance_id": instance.id if instance else None,
            "quotation_code": quotation.quotation_code or "",
            "bpm_no": instance.bpm_no if instance else (quotation.bpm_no or ""),
            "route_scene": route_scene,
            "trigger_source": trigger_source,
            "error_message": (error_message or "").strip(),
            "focus_material_ids": focus_materials,
            "focus_process_ids": focus_processes,
            "material_count": len(materials),
            "process_count": len(processes),
            "count_mismatch": len(materials) != len(processes),
        },
        "quotation": {
            "customer_name": quotation.customer_name or "",
            "product_spec": quotation.product_spec or "",
            "structure": quotation.structure or "",
            "remark": quotation.remark or "",
        },
        "materials": [
            {
                "id": item.id,
                "seq_no": item.seq_no,
                "process_name": item.process_name or "",
                "spec_detail": item.spec_detail or "",
                "process_code": item.process_code or "",
                "unit_usage": _decimal_text(item.unit_usage),
                "unit_price": _decimal_text(item.unit_price),
            }
            for item in materials
        ],
        "processes": [
            {
                "id": item.id,
                "process_name": item.process_name or "",
                "process_code": item.process_code or "",
                "fixed_fee": _decimal_text(item.fixed_fee),
                "startup_loss_wire": _decimal_text(item.startup_loss_wire),
                "total_waste_glue": _decimal_text(item.total_waste_glue),
            }
            for item in processes
        ],
    }


def _build_prompt(
    policy_rules_raw: str,
    candidate_skills: list[dict],
    input_snapshot: dict,
    route_scene: str,
    trigger_source: str,
) -> str:
    meta = input_snapshot.get("meta") or {}
    return ROUTING_USER_PROMPT.format(
        route_scene=route_scene,
        trigger_source=trigger_source,
        error_message=meta.get("error_message") or "无",
        focus_material_ids=json.dumps(meta.get("focus_material_ids") or [], ensure_ascii=False),
        focus_process_ids=json.dumps(meta.get("focus_process_ids") or [], ensure_ascii=False),
        candidate_skills_json=json.dumps(candidate_skills, ensure_ascii=False, indent=2),
        policy_rules_json=json.dumps(parse_prompt_rules(policy_rules_raw), ensure_ascii=False, indent=2),
        input_snapshot_json=json.dumps(input_snapshot, ensure_ascii=False, indent=2),
    )


def _build_route_plan_prompt(
    policy_rules_raw: str,
    candidate_skills: list[dict],
    input_snapshot: dict,
    route_scene: str,
    trigger_source: str,
) -> str:
    meta = input_snapshot.get("meta") or {}
    return ROUTE_PLAN_USER_PROMPT.format(
        route_scene=route_scene,
        trigger_source=trigger_source,
        error_message=meta.get("error_message") or "无",
        focus_material_ids=json.dumps(meta.get("focus_material_ids") or [], ensure_ascii=False),
        focus_process_ids=json.dumps(meta.get("focus_process_ids") or [], ensure_ascii=False),
        candidate_skills_json=json.dumps(candidate_skills, ensure_ascii=False, indent=2),
        policy_rules_json=json.dumps(parse_prompt_rules(policy_rules_raw), ensure_ascii=False, indent=2),
        input_snapshot_json=json.dumps(input_snapshot, ensure_ascii=False, indent=2),
    )


def _parse_json_object(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("LLM 返回为空")

    def _load(candidate: str) -> dict:
        data = json.loads(candidate)
        if not isinstance(data, dict):
            raise ValueError("LLM 返回的 JSON 根节点必须是对象")
        return data

    try:
        return _load(text)
    except Exception:
        match = _JSON_BLOCK_RE.search(text)
        if match:
            return _load(match.group(1).strip())
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return _load(text[start:end + 1])
        raise ValueError("LLM 返回中未找到合法 JSON 对象")


def _validate_decision(
    raw_decision: dict,
    quotation: QuotationMain,
    candidate_skill_ids: list[str],
    confidence_threshold,
) -> dict:
    allowed_keys = set(DEFAULT_DECISION_JSON.keys())
    extra_keys = sorted(set(raw_decision.keys()) - allowed_keys)
    if extra_keys:
        raise ValueError("decision_json 包含未允许字段: " + "、".join(extra_keys))

    decision = dict(DEFAULT_DECISION_JSON)
    decision.update(raw_decision)
    decision["route_type"] = str(decision.get("route_type") or "").strip()
    decision["target_skill"] = str(decision.get("target_skill") or "").strip()
    decision["target_subtype"] = str(decision.get("target_subtype") or "").strip()
    decision["mapping_mode"] = str(decision.get("mapping_mode") or "").strip()
    decision["reason"] = str(decision.get("reason") or "").strip()
    decision["matched_material_ids"] = _normalize_int_list(decision.get("matched_material_ids"))
    decision["matched_process_ids"] = _normalize_int_list(decision.get("matched_process_ids"))
    decision["manual_review_required"] = bool(decision.get("manual_review_required", False))
    decision["confidence"] = float(_coerce_probability(decision.get("confidence"), "confidence"))

    if decision["route_type"] not in {"route_skill", "manual_review", "reject"}:
        raise ValueError("route_type 必须是 route_skill/manual_review/reject")
    if decision["mapping_mode"] not in {"one_to_one", "one_to_many", "many_to_one", "ambiguous", "none"}:
        raise ValueError("mapping_mode 不合法")
    if decision["route_type"] == "route_skill" and not decision["target_skill"]:
        raise ValueError("route_type=route_skill 时必须提供 target_skill")
    if decision["target_skill"] and decision["target_skill"] not in candidate_skill_ids:
        raise ValueError("target_skill 不在候选 skill 列表中")

    material_ids = {item.id for item in quotation.materials if not item.deleted and item.id is not None}
    process_ids = {item.id for item in quotation.processes if not item.deleted and item.id is not None}
    if not set(decision["matched_material_ids"]).issubset(material_ids):
        raise ValueError("matched_material_ids 包含不属于当前报价单的行")
    if not set(decision["matched_process_ids"]).issubset(process_ids):
        raise ValueError("matched_process_ids 包含不属于当前报价单的行")

    threshold = Decimal(str(confidence_threshold)) if confidence_threshold is not None else None
    if threshold is not None and Decimal(str(decision["confidence"])) < threshold:
        decision["manual_review_required"] = True
        if not decision["reason"]:
            decision["reason"] = "置信度低于策略阈值"

    if decision["route_type"] == "reject":
        final_action = "reject"
        decision["manual_review_required"] = True
    elif decision["route_type"] == "manual_review" or decision["manual_review_required"]:
        final_action = "manual_review"
    else:
        final_action = "route_skill"

    error_message = None if final_action == "route_skill" else (decision["reason"] or "需要人工复核")
    return {
        "decision": decision,
        "final_action": final_action,
        "error_message": error_message,
    }


def _write_non_llm_decision(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
    route_scene: str,
    trigger_source: str,
    input_snapshot: dict,
    candidate_skill_ids: list[str],
    final_action: str,
    reason: str,
    policy=None,
    llm_model: str | None = None,
) -> dict:
    decision = dict(DEFAULT_DECISION_JSON)
    decision.update({
        "route_type": "reject" if final_action == "reject" else "manual_review",
        "reason": reason,
        "manual_review_required": True,
    })
    stored_error_message = reason if final_action == "reject" else None
    run = create_routing_decision_run(
        db=db,
        quotation=quotation,
        instance=instance,
        operator=operator,
        route_scene=route_scene,
        trigger_source=trigger_source,
        input_snapshot=input_snapshot,
        candidate_skills=candidate_skill_ids,
        final_action=final_action,
        policy=policy,
        llm_model=llm_model,
        decision_json=decision,
        confidence=0,
        final_skill=None,
        adopt_status="pending",
        error_message=reason,
    )
    return {
        "routing_run_id": run.id,
        "policy_id": policy.id if policy else None,
        "policy_name": policy.policy_name if policy else "",
        "decision": decision,
        "final_action": final_action,
        "final_skill": "",
        "adopt_status": run.adopt_status,
        "error_message": reason,
    }


def _write_non_llm_route_plan(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
    route_scene: str,
    trigger_source: str,
    input_snapshot: dict,
    candidate_skill_ids: list[str],
    final_action: str,
    reason: str,
    policy=None,
    llm_model: str | None = None,
) -> dict:
    plan = _default_route_plan(
        quotation,
        instance,
        reason,
        summary_status="reject" if final_action == "reject" else "manual_review_only",
    )
    run = create_routing_decision_run(
        db=db,
        quotation=quotation,
        instance=instance,
        operator=operator,
        route_scene=route_scene,
        trigger_source=trigger_source,
        input_snapshot=input_snapshot,
        candidate_skills=candidate_skill_ids,
        final_action=final_action,
        policy=policy,
        llm_model=llm_model,
        decision_json=plan,
        confidence=0,
        final_skill="route_plan",
        adopt_status="pending",
        error_message=stored_error_message,
    )
    return {
        "routing_run_id": run.id,
        "policy_id": policy.id if policy else None,
        "policy_name": policy.policy_name if policy else "",
        "route_plan": plan,
        "final_action": final_action,
        "final_skill": "route_plan",
        "adopt_status": run.adopt_status,
        "error_message": stored_error_message,
    }


def _normalize_int_list(values) -> list[int]:
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise ValueError("matched ids 必须是数组")
    result = []
    seen = set()
    for item in values:
        try:
            value = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("matched ids 必须是整数数组") from exc
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_focus_ids(values, allowed_ids: set[int], label: str) -> list[int]:
    ids = _normalize_int_list(values)
    if not set(ids).issubset(allowed_ids):
        raise ValueError(f"{label} 包含不属于当前报价单的行")
    return ids


def _validate_route_plan(
    raw_plan: dict,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    candidate_skill_ids: list[str],
    confidence_threshold,
) -> dict:
    allowed_keys = {
        "route_type",
        "summary_status",
        "manual_review_required",
        "confidence",
        "reason",
        "quotation_code",
        "instance_id",
        "groups",
        "unmatched_material_ids",
        "unmatched_process_ids",
        "unmatched_details",
        "warnings",
        "meta",
    }
    extra_keys = sorted(set(raw_plan.keys()) - allowed_keys)
    if extra_keys:
        raise ValueError("route_plan 包含未允许字段: " + "、".join(extra_keys))

    plan = _default_route_plan(quotation, instance, "")
    plan.update(raw_plan)
    plan["route_type"] = "route_plan"
    plan["summary_status"] = str(plan.get("summary_status") or "").strip()
    plan["manual_review_required"] = bool(plan.get("manual_review_required", False))
    plan["confidence"] = float(_coerce_probability(plan.get("confidence"), "confidence"))
    plan["reason"] = str(plan.get("reason") or "").strip()
    plan["quotation_code"] = str(plan.get("quotation_code") or quotation.quotation_code or "").strip()
    plan["instance_id"] = instance.id if instance else quotation.id
    plan["warnings"] = _normalize_string_list(plan.get("warnings"))
    plan["meta"] = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}

    if plan["summary_status"] not in {"full_match", "partial_match", "manual_review_only", "reject"}:
        raise ValueError("summary_status 不合法")

    material_map = {
        item.id: item
        for item in quotation.materials
        if not item.deleted and item.id is not None
    }
    process_map = {
        item.id: item
        for item in quotation.processes
        if not item.deleted and item.id is not None
    }

    groups = []
    group_material_ids = set()
    group_process_ids = set()
    for index, item in enumerate(plan.get("groups") or [], start=1):
        if not isinstance(item, dict):
            raise ValueError("groups 的每一项必须是对象")
        group = {
            "group_id": str(item.get("group_id") or f"grp_{index}").strip() or f"grp_{index}",
            "step_order": int(item.get("step_order") or index),
            "group_type": str(item.get("group_type") or "unknown_stage").strip(),
            "target_skill": str(item.get("target_skill") or "").strip(),
            "match_status": str(item.get("match_status") or "unmatched").strip(),
            "manual_review_required": bool(item.get("manual_review_required", False)),
            "confidence": float(_coerce_probability(item.get("confidence"), f"group[{index}].confidence")),
            "material_ids": _normalize_int_list(item.get("material_ids")),
            "process_ids": _normalize_int_list(item.get("process_ids")),
            "material_names": _normalize_string_list(item.get("material_names")),
            "process_names": _normalize_string_list(item.get("process_names")),
            "reason": str(item.get("reason") or "").strip(),
            "rule_hits": _normalize_string_list(item.get("rule_hits")),
        }
        if group["step_order"] <= 0:
            raise ValueError("groups.step_order 必须是正整数")
        if group["group_type"] not in {"conductor_stage", "glue_stage", "price_summary_stage", "mixed_stage", "unknown_stage"}:
            raise ValueError("groups.group_type 不合法")
        if group["match_status"] not in {"matched", "partially_matched", "ambiguous", "unmatched"}:
            raise ValueError("groups.match_status 不合法")
        if group["target_skill"] and group["target_skill"] not in candidate_skill_ids:
            raise ValueError("groups.target_skill 不在候选 skill 列表中")
        if not set(group["material_ids"]).issubset(material_map.keys()):
            raise ValueError("groups.material_ids 包含不属于当前报价单的行")
        if not set(group["process_ids"]).issubset(process_map.keys()):
            raise ValueError("groups.process_ids 包含不属于当前报价单的行")
        if not group["material_names"]:
            group["material_names"] = [_material_label(material_map[item_id]) for item_id in group["material_ids"]]
        if not group["process_names"]:
            group["process_names"] = [_process_label(process_map[item_id]) for item_id in group["process_ids"]]
        group_material_ids.update(group["material_ids"])
        group_process_ids.update(group["process_ids"])
        groups.append(group)
    groups.sort(key=lambda item: (item["step_order"], item["group_id"]))
    plan["groups"] = groups

    plan["unmatched_material_ids"] = _normalize_int_list(plan.get("unmatched_material_ids"))
    plan["unmatched_process_ids"] = _normalize_int_list(plan.get("unmatched_process_ids"))
    if not set(plan["unmatched_material_ids"]).issubset(material_map.keys()):
        raise ValueError("unmatched_material_ids 包含不属于当前报价单的行")
    if not set(plan["unmatched_process_ids"]).issubset(process_map.keys()):
        raise ValueError("unmatched_process_ids 包含不属于当前报价单的行")

    unmatched_details = []
    for index, item in enumerate(plan.get("unmatched_details") or [], start=1):
        if not isinstance(item, dict):
            raise ValueError("unmatched_details 的每一项必须是对象")
        item_type = str(item.get("item_type") or "").strip()
        if item_type not in {"material", "process"}:
            raise ValueError("unmatched_details.item_type 必须是 material 或 process")
        item_id = int(item.get("item_id"))
        if item_type == "material" and item_id not in material_map:
            raise ValueError("unmatched_details.item_id 不属于当前报价单材料行")
        if item_type == "process" and item_id not in process_map:
            raise ValueError("unmatched_details.item_id 不属于当前报价单制程行")
        suggested_skill = str(item.get("suggested_skill") or "").strip()
        if suggested_skill and suggested_skill not in candidate_skill_ids:
            raise ValueError("unmatched_details.suggested_skill 不在候选 skill 列表中")
        unmatched_details.append({
            "item_type": item_type,
            "item_id": item_id,
            "item_name": str(item.get("item_name") or (
                _material_label(material_map[item_id]) if item_type == "material" else _process_label(process_map[item_id])
            )).strip(),
            "status": "unmatched",
            "suggested_skill": suggested_skill,
            "manual_review_required": bool(item.get("manual_review_required", True)),
            "reason": str(item.get("reason") or "").strip(),
        })
    plan["unmatched_details"] = unmatched_details

    threshold = Decimal(str(confidence_threshold)) if confidence_threshold is not None else None
    if threshold is not None and Decimal(str(plan["confidence"])) < threshold:
        plan["manual_review_required"] = True
        if plan["summary_status"] == "full_match":
            plan["summary_status"] = "partial_match"

    if plan["summary_status"] in {"partial_match", "manual_review_only", "reject"}:
        plan["manual_review_required"] = True
    if any(group["manual_review_required"] for group in groups):
        plan["manual_review_required"] = True

    if plan["summary_status"] == "reject":
        final_action = "reject"
    elif plan["summary_status"] == "full_match" and not plan["manual_review_required"] and groups:
        final_action = "route_skill"
    else:
        final_action = "manual_review"

    error_message = plan["reason"] if final_action == "reject" else None
    return {
        "plan": plan,
        "final_action": final_action,
        "error_message": error_message,
    }


def _default_route_plan(
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    reason: str,
    summary_status: str = "manual_review_only",
) -> dict:
    return {
        "route_type": "route_plan",
        "summary_status": summary_status,
        "manual_review_required": True,
        "confidence": 0,
        "reason": reason,
        "quotation_code": quotation.quotation_code or "",
        "instance_id": instance.id if instance else quotation.id,
        "groups": [],
        "unmatched_material_ids": [],
        "unmatched_process_ids": [],
        "unmatched_details": [],
        "warnings": [],
        "meta": {},
        "target_skill": "",
        "target_subtype": "",
        "mapping_mode": "none",
        "matched_material_ids": [],
        "matched_process_ids": [],
    }


def _normalize_string_list(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise ValueError("字符串数组字段必须是数组")
    result = []
    for item in values:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _material_label(item) -> str:
    return (getattr(item, "process_name", None) or getattr(item, "spec_detail", None) or "").strip()


def _process_label(item) -> str:
    return (getattr(item, "process_name", None) or getattr(item, "process_code", None) or "").strip()


def _coerce_probability(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if result < 0 or result > 1:
        raise ValueError(f"{label}必须在 0 到 1 之间")
    return result


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
