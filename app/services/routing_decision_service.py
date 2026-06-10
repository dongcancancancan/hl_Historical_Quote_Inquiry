from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.calculation_trace import QuotationCalculationRun
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.models.routing import QuotationRoutingDecisionRun, QuotationRoutingPolicy


DEFAULT_DECISION_JSON = {
    "route_type": "route_skill",
    "target_skill": "",
    "target_subtype": "",
    "mapping_mode": "one_to_one",
    "confidence": 0,
    "reason": "",
    "matched_material_ids": [],
    "matched_process_ids": [],
    "manual_review_required": False,
}

ROUTE_PLAN_ALLOWED_GROUP_TYPES = {
    "conductor_stage",
    "glue_stage",
    "price_summary_stage",
    "mixed_stage",
    "unknown_stage",
}

ROUTE_PLAN_ALLOWED_MATCH_STATUS = {
    "matched",
    "partially_matched",
    "ambiguous",
    "unmatched",
}

ROUTE_PLAN_ALLOWED_SUMMARY_STATUS = {
    "full_match",
    "partial_match",
    "manual_review_only",
    "reject",
}


DEFAULT_HUMAN_REVIEW = {
    "is_correct": None,
    "expected_skill": "",
    "comment": "",
}


def create_routing_decision_run(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
    route_scene: str,
    trigger_source: str,
    input_snapshot,
    candidate_skills,
    final_action: str,
    policy: QuotationRoutingPolicy | None = None,
    llm_model: str | None = None,
    llm_prompt_text: str | None = None,
    llm_response_text: str | None = None,
    decision_json=None,
    confidence=None,
    final_skill: str | None = None,
    adopt_status: str = "pending",
    error_message: str | None = None,
    calculation_run: QuotationCalculationRun | None = None,
) -> QuotationRoutingDecisionRun:
    normalized_decision = normalize_decision_json(decision_json)
    effective_confidence = _coalesce_confidence(confidence, normalized_decision.get("confidence"))
    run = QuotationRoutingDecisionRun(
        quotation_main_id=quotation.id,
        bpm_instance_id=instance.id if instance else None,
        calculation_run_id=calculation_run.id if calculation_run else None,
        quotation_code=quotation.quotation_code or "",
        bpm_no=instance.bpm_no if instance else (quotation.bpm_no or ""),
        tenant_id=quotation.tenant_id,
        policy_id=policy.id if policy else None,
        route_scene=_required_text(route_scene, "route_scene"),
        trigger_source=_required_text(trigger_source, "trigger_source"),
        input_snapshot=_dump_json(_normalize_input_snapshot(input_snapshot)),
        candidate_skills=_dump_json(_normalize_candidate_skills(candidate_skills)),
        llm_model=_optional_text(llm_model or (policy.llm_model if policy else None)),
        llm_prompt_text=_optional_text(llm_prompt_text),
        llm_response_text=_optional_text(llm_response_text),
        decision_json=_dump_json(normalized_decision),
        confidence=effective_confidence,
        final_action=_required_text(final_action, "final_action"),
        final_skill=_optional_text(final_skill or normalized_decision.get("target_skill")),
        adopt_status=_adopt_status(adopt_status),
        error_message=_optional_text(error_message),
        operator=_required_text(operator, "operator"),
        create_time=datetime.now(),
    )
    db.add(run)
    db.flush()
    return run


def update_routing_decision_run(
    run: QuotationRoutingDecisionRun,
    *,
    llm_model: str | None = None,
    llm_prompt_text: str | None = None,
    llm_response_text: str | None = None,
    decision_json=None,
    confidence=None,
    final_action: str | None = None,
    final_skill: str | None = None,
    adopt_status: str | None = None,
    error_message: str | None = None,
    calculation_run: QuotationCalculationRun | None = None,
) -> QuotationRoutingDecisionRun:
    if llm_model is not None:
        run.llm_model = _optional_text(llm_model)
    if llm_prompt_text is not None:
        run.llm_prompt_text = _optional_text(llm_prompt_text)
    if llm_response_text is not None:
        run.llm_response_text = _optional_text(llm_response_text)
    if decision_json is not None:
        normalized_decision = normalize_decision_json(decision_json)
        run.decision_json = _dump_json(normalized_decision)
        if final_skill is None:
            final_skill = normalized_decision.get("target_skill")
        if confidence is None:
            confidence = normalized_decision.get("confidence")
    if confidence is not None:
        run.confidence = _probability(confidence, "confidence")
    if final_action is not None:
        run.final_action = _required_text(final_action, "final_action")
    if final_skill is not None:
        run.final_skill = _optional_text(final_skill)
    if adopt_status is not None:
        run.adopt_status = _adopt_status(adopt_status)
    if error_message is not None:
        run.error_message = _optional_text(error_message)
    if calculation_run is not None:
        run.calculation_run_id = calculation_run.id
    return run


def mark_decision_adopted(
    db: Session,
    run: QuotationRoutingDecisionRun | None,
    reject_others: bool = False,
) -> None:
    if not run:
        return
    if reject_others:
        db.query(QuotationRoutingDecisionRun).filter(
            QuotationRoutingDecisionRun.id != run.id,
            QuotationRoutingDecisionRun.quotation_main_id == run.quotation_main_id,
            QuotationRoutingDecisionRun.bpm_instance_id == run.bpm_instance_id,
            QuotationRoutingDecisionRun.adopt_status == "adopted",
        ).update({"adopt_status": "rejected"}, synchronize_session=False)
    run.adopt_status = "adopted"


def latest_routing_decision_run(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None = None,
    route_scene: str | None = None,
) -> QuotationRoutingDecisionRun | None:
    query = db.query(QuotationRoutingDecisionRun).filter(
        QuotationRoutingDecisionRun.quotation_main_id == quotation.id,
    )
    if instance:
        query = query.filter(QuotationRoutingDecisionRun.bpm_instance_id == instance.id)
    else:
        query = query.filter(QuotationRoutingDecisionRun.bpm_instance_id.is_(None))
    if route_scene:
        query = query.filter(QuotationRoutingDecisionRun.route_scene == route_scene)
    return query.order_by(
        QuotationRoutingDecisionRun.create_time.desc(),
        QuotationRoutingDecisionRun.id.desc(),
    ).first()


def list_routing_decision_runs(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None = None,
    route_scene: str | None = None,
    adopt_status: str | None = None,
    final_action: str | None = None,
    final_skill: str | None = None,
    manual_review_required: bool | None = None,
    confidence_min=None,
    confidence_max=None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
) -> list[QuotationRoutingDecisionRun]:
    rows = _query_routing_decision_runs(
        db=db,
        quotation=quotation,
        instance=instance,
        route_scene=route_scene,
        adopt_status=adopt_status,
        final_action=final_action,
        final_skill=final_skill,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        date_from=date_from,
        date_to=date_to,
    )
    filtered = _filter_by_decision_flags(rows, manual_review_required=manual_review_required)
    safe_limit = max(1, min(int(limit or 20), 100))
    return filtered[:safe_limit]


def summarize_routing_decision_runs(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None = None,
    route_scene: str | None = None,
    adopt_status: str | None = None,
    final_action: str | None = None,
    final_skill: str | None = None,
    manual_review_required: bool | None = None,
    confidence_min=None,
    confidence_max=None,
    date_from: date | None = None,
    date_to: date | None = None,
    low_confidence_threshold=None,
) -> dict:
    rows = _query_routing_decision_runs(
        db=db,
        quotation=quotation,
        instance=instance,
        route_scene=route_scene,
        adopt_status=adopt_status,
        final_action=final_action,
        final_skill=final_skill,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        date_from=date_from,
        date_to=date_to,
    )
    rows = _filter_by_decision_flags(rows, manual_review_required=manual_review_required)
    threshold = _optional_probability(low_confidence_threshold, "low_confidence_threshold")
    confidence_values = [Decimal(str(row.confidence)) for row in rows if row.confidence is not None]
    by_skill: dict[str, int] = {}
    for row in rows:
        if row.final_skill:
            by_skill[row.final_skill] = by_skill.get(row.final_skill, 0) + 1
    avg_confidence = None
    if confidence_values:
        avg_confidence = sum(confidence_values) / Decimal(len(confidence_values))

    return {
        "total": len(rows),
        "route_skill": sum(1 for row in rows if row.final_action == "route_skill"),
        "manual_review": sum(1 for row in rows if row.final_action == "manual_review"),
        "reject": sum(1 for row in rows if row.final_action == "reject"),
        "avg_confidence": _decimal_text(avg_confidence),
        "by_skill": by_skill,
        "low_confidence_count": sum(
            1
            for row in rows
            if threshold is not None and row.confidence is not None and Decimal(str(row.confidence)) < threshold
        ),
    }


def get_routing_decision_run(
    db: Session,
    run_id: int,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None = None,
) -> QuotationRoutingDecisionRun | None:
    query = db.query(QuotationRoutingDecisionRun).filter(
        QuotationRoutingDecisionRun.id == run_id,
        QuotationRoutingDecisionRun.quotation_main_id == quotation.id,
    )
    if instance:
        query = query.filter(QuotationRoutingDecisionRun.bpm_instance_id == instance.id)
    return query.first()


def update_routing_human_review(
    run: QuotationRoutingDecisionRun,
    *,
    is_correct: bool | None = None,
    expected_skill: str | None = None,
    comment: str | None = None,
) -> QuotationRoutingDecisionRun:
    decision = normalize_decision_json(_load_json(run.decision_json, default={}))
    current_review = decision.get("human_review") or dict(DEFAULT_HUMAN_REVIEW)
    updated_review = {
        "is_correct": current_review.get("is_correct"),
        "expected_skill": str(current_review.get("expected_skill") or "").strip(),
        "comment": str(current_review.get("comment") or "").strip(),
    }
    if is_correct is not None:
        updated_review["is_correct"] = bool(is_correct)
    if expected_skill is not None:
        updated_review["expected_skill"] = str(expected_skill or "").strip()
    if comment is not None:
        updated_review["comment"] = str(comment or "").strip()
    decision["human_review"] = updated_review
    run.decision_json = _dump_json(decision)
    return run


def serialize_routing_decision_run(run: QuotationRoutingDecisionRun) -> dict:
    decision = normalize_decision_json(_load_json(run.decision_json, default=DEFAULT_DECISION_JSON))
    return {
        "id": run.id,
        "quotation_main_id": run.quotation_main_id,
        "bpm_instance_id": run.bpm_instance_id,
        "calculation_run_id": run.calculation_run_id,
        "quotation_code": run.quotation_code or "",
        "bpm_no": run.bpm_no or "",
        "tenant_id": run.tenant_id or "",
        "policy_id": run.policy_id,
        "route_scene": run.route_scene or "",
        "trigger_source": run.trigger_source or "",
        "input_snapshot": _load_json(run.input_snapshot, default={}),
        "candidate_skills": _load_json(run.candidate_skills, default=[]),
        "llm_model": run.llm_model or "",
        "llm_prompt_text": run.llm_prompt_text or "",
        "llm_response_text": run.llm_response_text or "",
        "decision_json": decision,
        "confidence": _decimal_text(run.confidence),
        "final_action": run.final_action or "",
        "final_skill": run.final_skill or "",
        "adopt_status": run.adopt_status or "",
        "error_message": run.error_message or "",
        "operator": run.operator or "",
        "create_time": run.create_time.isoformat() if run.create_time else None,
    }


def normalize_decision_json(raw_value) -> dict:
    data = raw_value
    if data in (None, ""):
        data = {}
    elif isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("decision_json 不是合法JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("decision_json 必须是对象")

    normalized = dict(DEFAULT_DECISION_JSON)
    normalized["route_type"] = _required_text(data.get("route_type", normalized["route_type"]), "route_type")
    normalized["target_skill"] = str(data.get("target_skill", "") or "").strip()
    normalized["target_subtype"] = str(data.get("target_subtype", "") or "").strip()
    normalized["mapping_mode"] = _required_text(data.get("mapping_mode", normalized["mapping_mode"]), "mapping_mode")
    normalized["confidence"] = float(_probability(data.get("confidence", normalized["confidence"]), "decision_json.confidence"))
    normalized["reason"] = str(data.get("reason", "") or "").strip()
    normalized["matched_material_ids"] = _normalize_int_list(data.get("matched_material_ids"))
    normalized["matched_process_ids"] = _normalize_int_list(data.get("matched_process_ids"))
    normalized["manual_review_required"] = bool(data.get("manual_review_required", False))
    if normalized["route_type"] == "route_plan":
        normalized["summary_status"] = _required_text(
            data.get("summary_status", "manual_review_only"),
            "decision_json.summary_status",
        )
        if normalized["summary_status"] not in ROUTE_PLAN_ALLOWED_SUMMARY_STATUS:
            raise ValueError("decision_json.summary_status 不合法")
        normalized["quotation_code"] = str(data.get("quotation_code") or "").strip()
        normalized["instance_id"] = _optional_int(data.get("instance_id"), "decision_json.instance_id")
        normalized["groups"] = _normalize_route_plan_groups(data.get("groups"))
        normalized["unmatched_material_ids"] = _normalize_int_list(data.get("unmatched_material_ids"))
        normalized["unmatched_process_ids"] = _normalize_int_list(data.get("unmatched_process_ids"))
        normalized["unmatched_details"] = _normalize_unmatched_details(data.get("unmatched_details"))
        normalized["warnings"] = _normalize_string_list(data.get("warnings"))
        normalized["meta"] = _normalize_meta_object(data.get("meta"))
    human_review = data.get("human_review")
    if human_review not in (None, ""):
        if not isinstance(human_review, dict):
            raise ValueError("decision_json.human_review 必须是对象")
        normalized["human_review"] = {
            "is_correct": (
                None if human_review.get("is_correct") is None else bool(human_review.get("is_correct"))
            ),
            "expected_skill": str(human_review.get("expected_skill") or "").strip(),
            "comment": str(human_review.get("comment") or "").strip(),
        }
    return normalized


def _normalize_route_plan_groups(raw_value) -> list[dict]:
    if raw_value in (None, ""):
        return []
    if not isinstance(raw_value, list):
        raise ValueError("decision_json.groups 必须是数组")
    groups = []
    for index, item in enumerate(raw_value, start=1):
        if not isinstance(item, dict):
            raise ValueError("decision_json.groups 的每一项必须是对象")
        group_type = str(item.get("group_type") or "unknown_stage").strip()
        if group_type not in ROUTE_PLAN_ALLOWED_GROUP_TYPES:
            raise ValueError("decision_json.groups.group_type 不合法")
        match_status = str(item.get("match_status") or "unmatched").strip()
        if match_status not in ROUTE_PLAN_ALLOWED_MATCH_STATUS:
            raise ValueError("decision_json.groups.match_status 不合法")
        groups.append({
            "group_id": str(item.get("group_id") or f"grp_{index}").strip() or f"grp_{index}",
            "step_order": _positive_int(item.get("step_order", index), "decision_json.groups.step_order"),
            "group_type": group_type,
            "target_skill": str(item.get("target_skill") or "").strip(),
            "match_status": match_status,
            "manual_review_required": bool(item.get("manual_review_required", False)),
            "confidence": float(_probability(item.get("confidence", 0), "decision_json.groups.confidence")),
            "material_ids": _normalize_int_list(item.get("material_ids")),
            "process_ids": _normalize_int_list(item.get("process_ids")),
            "material_names": _normalize_string_list(item.get("material_names")),
            "process_names": _normalize_string_list(item.get("process_names")),
            "reason": str(item.get("reason") or "").strip(),
            "rule_hits": _normalize_string_list(item.get("rule_hits")),
        })
    return groups


def _normalize_unmatched_details(raw_value) -> list[dict]:
    if raw_value in (None, ""):
        return []
    if not isinstance(raw_value, list):
        raise ValueError("decision_json.unmatched_details 必须是数组")
    details = []
    for item in raw_value:
        if not isinstance(item, dict):
            raise ValueError("decision_json.unmatched_details 的每一项必须是对象")
        item_type = str(item.get("item_type") or "").strip()
        if item_type not in {"material", "process"}:
            raise ValueError("decision_json.unmatched_details.item_type 必须是 material 或 process")
        details.append({
            "item_type": item_type,
            "item_id": _positive_int(item.get("item_id"), "decision_json.unmatched_details.item_id"),
            "item_name": str(item.get("item_name") or "").strip(),
            "status": str(item.get("status") or "unmatched").strip() or "unmatched",
            "suggested_skill": str(item.get("suggested_skill") or "").strip(),
            "manual_review_required": bool(item.get("manual_review_required", True)),
            "reason": str(item.get("reason") or "").strip(),
        })
    return details


def _normalize_string_list(raw_value) -> list[str]:
    if raw_value in (None, ""):
        return []
    if not isinstance(raw_value, list):
        raise ValueError("字符串列表字段必须是数组")
    result = []
    for item in raw_value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _normalize_meta_object(raw_value) -> dict:
    if raw_value in (None, ""):
        return {}
    if not isinstance(raw_value, dict):
        raise ValueError("decision_json.meta 必须是对象")
    return raw_value


def _optional_int(value, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是整数") from exc


def _positive_int(value, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是正整数") from exc
    if result <= 0:
        raise ValueError(f"{label} 必须是正整数")
    return result


def _query_routing_decision_runs(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None = None,
    route_scene: str | None = None,
    adopt_status: str | None = None,
    final_action: str | None = None,
    final_skill: str | None = None,
    confidence_min=None,
    confidence_max=None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[QuotationRoutingDecisionRun]:
    query = db.query(QuotationRoutingDecisionRun).filter(
        QuotationRoutingDecisionRun.quotation_main_id == quotation.id,
    )
    if instance:
        query = query.filter(QuotationRoutingDecisionRun.bpm_instance_id == instance.id)
    else:
        query = query.filter(QuotationRoutingDecisionRun.bpm_instance_id.is_(None))
    if route_scene:
        query = query.filter(QuotationRoutingDecisionRun.route_scene == route_scene)
    if adopt_status:
        query = query.filter(QuotationRoutingDecisionRun.adopt_status == _adopt_status(adopt_status))
    if final_action:
        query = query.filter(QuotationRoutingDecisionRun.final_action == _final_action(final_action))
    if final_skill:
        query = query.filter(QuotationRoutingDecisionRun.final_skill == str(final_skill).strip())
    min_value = _optional_probability(confidence_min, "confidence_min")
    max_value = _optional_probability(confidence_max, "confidence_max")
    if min_value is not None:
        query = query.filter(QuotationRoutingDecisionRun.confidence >= min_value)
    if max_value is not None:
        query = query.filter(QuotationRoutingDecisionRun.confidence <= max_value)
    if date_from:
        query = query.filter(QuotationRoutingDecisionRun.create_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(QuotationRoutingDecisionRun.create_time < datetime.combine(date_to, datetime.max.time()))
    return query.order_by(
        QuotationRoutingDecisionRun.create_time.desc(),
        QuotationRoutingDecisionRun.id.desc(),
    ).all()


def _filter_by_decision_flags(
    rows: list[QuotationRoutingDecisionRun],
    *,
    manual_review_required: bool | None = None,
) -> list[QuotationRoutingDecisionRun]:
    if manual_review_required is None:
        return rows
    result = []
    for row in rows:
        decision = normalize_decision_json(_load_json(row.decision_json, default={}))
        if bool(decision.get("manual_review_required", False)) == manual_review_required:
            result.append(row)
    return result


def _normalize_input_snapshot(raw_value):
    data = raw_value
    if data in (None, ""):
        return {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("input_snapshot 不是合法JSON") from exc
    if not isinstance(data, (dict, list)):
        raise ValueError("input_snapshot 必须是对象或数组")
    return data


def _normalize_candidate_skills(raw_value) -> list[str]:
    data = raw_value
    if data in (None, ""):
        return []
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not isinstance(data, list):
        raise ValueError("candidate_skills 必须是数组")

    result = []
    seen = set()
    for item in data:
        skill = str(item or "").strip()
        if not skill or skill in seen:
            continue
        seen.add(skill)
        result.append(skill)
    return result


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


def _adopt_status(value) -> str:
    status = str(value or "pending").strip().lower()
    if status not in {"pending", "adopted", "rejected"}:
        raise ValueError("adopt_status 必须是 pending/adopted/rejected")
    return status


def _final_action(value) -> str:
    action = str(value or "").strip().lower()
    if action not in {"route_skill", "manual_review", "reject"}:
        raise ValueError("final_action 必须是 route_skill/manual_review/reject")
    return action


def _coalesce_confidence(explicit_value, decision_value):
    value = explicit_value if explicit_value not in (None, "") else decision_value
    if value in (None, ""):
        return None
    return _probability(value, "confidence")


def _probability(value, label: str):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if result < 0 or result > 1:
        raise ValueError(f"{label}必须在 0 到 1 之间")
    return result


def _optional_probability(value, label: str):
    if value in (None, ""):
        return None
    return _probability(value, label)


def _required_text(value, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label}不能为空")
    return result


def _optional_text(value) -> str | None:
    result = str(value).strip() if value not in (None, "") else ""
    return result or None


def _load_json(raw_value: str | None, default):
    try:
        data = json.loads(raw_value or "null")
        return data if data is not None else default
    except Exception:
        return default


def _dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=_json_default)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
