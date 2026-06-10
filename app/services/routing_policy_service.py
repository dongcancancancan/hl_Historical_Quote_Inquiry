from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.routing import QuotationRoutingPolicy


DEFAULT_ROUTE_SCOPE = "cost_analysis"
DEFAULT_PROMPT_RULES = {
    "role_rules": [],
    "business_rules": [],
    "output_rules": [],
}
DEFAULT_POLICY_NAME = "默认LLM路由策略"
DEFAULT_CONFIDENCE_THRESHOLD = Decimal("0.75")
DEFAULT_POLICY_RULES = {
    "role_rules": [
        "你只做 skill 路由决策，不做金额计算。",
        "只能从候选 skill 中选择 target_skill。",
    ],
    "business_rules": [
        "未知制程或上下部分数量不对应时，优先判断是否可复用现有 skill。",
        "导体、铜绞、编织相关优先考虑 conductor_material_and_process。",
        "胶料、外购料、绝缘、外被、包带、倒线、集合相关优先考虑 glue_external_and_process。",
        "无法可靠判断时必须返回 manual_review_required=true。",
        "matched_material_ids 和 matched_process_ids 必须只使用当前报价单真实存在的行。",
    ],
    "output_rules": [
        "只输出固定 JSON schema，不增加额外字段。",
        "不得输出公式、金额、价格、成本或单价。",
    ],
}


def list_policies(
    db: Session,
    tenant_id: str | None = None,
    route_scope: str | None = None,
    include_deleted: bool = False,
) -> list[QuotationRoutingPolicy]:
    query = db.query(QuotationRoutingPolicy)
    if tenant_id:
        query = query.filter(QuotationRoutingPolicy.tenant_id == tenant_id)
    if route_scope:
        query = query.filter(QuotationRoutingPolicy.route_scope == route_scope)
    if not include_deleted:
        query = query.filter(QuotationRoutingPolicy.deleted == False)
    return query.order_by(
        QuotationRoutingPolicy.enabled.desc(),
        QuotationRoutingPolicy.update_time.desc(),
        QuotationRoutingPolicy.id.desc(),
    ).all()


def get_policy(db: Session, policy_id: int) -> QuotationRoutingPolicy | None:
    return (
        db.query(QuotationRoutingPolicy)
        .filter(
            QuotationRoutingPolicy.id == policy_id,
            QuotationRoutingPolicy.deleted == False,
        )
        .first()
    )


def get_active_policy(
    db: Session,
    tenant_id: str,
    route_scope: str = DEFAULT_ROUTE_SCOPE,
) -> QuotationRoutingPolicy | None:
    return (
        db.query(QuotationRoutingPolicy)
        .filter(
            QuotationRoutingPolicy.tenant_id == tenant_id,
            QuotationRoutingPolicy.route_scope == (route_scope or DEFAULT_ROUTE_SCOPE),
            QuotationRoutingPolicy.status == "published",
            QuotationRoutingPolicy.enabled == True,
            QuotationRoutingPolicy.deleted == False,
        )
        .order_by(QuotationRoutingPolicy.update_time.desc(), QuotationRoutingPolicy.id.desc())
        .first()
    )


def create_policy(db: Session, data: dict, operator: str) -> QuotationRoutingPolicy:
    tenant_id = _required_text(data.get("tenant_id"), "租户ID")
    route_scope = _route_scope(data.get("route_scope"))
    policy = QuotationRoutingPolicy(
        tenant_id=tenant_id,
        policy_name=_required_text(data.get("policy_name"), "策略名称"),
        status=_policy_status(data.get("status")),
        enabled=bool(data.get("enabled", False)),
        prompt_rules=_dump_json(_normalize_prompt_rules(data.get("prompt_rules"))),
        confidence_threshold=_optional_probability(data.get("confidence_threshold"), "置信度阈值"),
        version_no=_positive_int(data.get("version_no", 1), "版本号"),
        llm_model=_optional_text(data.get("llm_model")),
        route_scope=route_scope,
        remark=_optional_text(data.get("remark")),
        creator=operator,
        updater=operator,
        update_time=datetime.now(),
    )
    db.add(policy)
    db.flush()
    if policy.enabled:
        _disable_other_policies(db, policy)
    return policy


def update_policy(
    db: Session,
    policy: QuotationRoutingPolicy,
    data: dict,
    operator: str,
) -> QuotationRoutingPolicy:
    if "policy_name" in data:
        policy.policy_name = _required_text(data.get("policy_name"), "策略名称")
    if "status" in data:
        policy.status = _policy_status(data.get("status"))
    if "enabled" in data:
        policy.enabled = bool(data.get("enabled"))
    if "prompt_rules" in data:
        policy.prompt_rules = _dump_json(_normalize_prompt_rules(data.get("prompt_rules")))
    if "confidence_threshold" in data:
        policy.confidence_threshold = _optional_probability(data.get("confidence_threshold"), "置信度阈值")
    if "version_no" in data:
        policy.version_no = _positive_int(data.get("version_no"), "版本号")
    if "llm_model" in data:
        policy.llm_model = _optional_text(data.get("llm_model"))
    if "route_scope" in data:
        policy.route_scope = _route_scope(data.get("route_scope"))
    if "remark" in data:
        policy.remark = _optional_text(data.get("remark"))
    if "deleted" in data:
        policy.deleted = bool(data.get("deleted"))

    policy.updater = operator
    policy.update_time = datetime.now()

    if policy.enabled and not policy.deleted:
        _disable_other_policies(db, policy)
    return policy


def activate_policy(db: Session, policy: QuotationRoutingPolicy, operator: str) -> QuotationRoutingPolicy:
    policy.enabled = True
    policy.status = "published"
    policy.deleted = False
    policy.updater = operator
    policy.update_time = datetime.now()
    _disable_other_policies(db, policy)
    return policy


def ensure_default_policy(
    db: Session,
    tenant_id: str,
    operator: str = "SYSTEM",
    route_scope: str = DEFAULT_ROUTE_SCOPE,
) -> QuotationRoutingPolicy:
    tenant = _required_text(tenant_id, "租户ID")
    scope = _route_scope(route_scope)
    active = get_active_policy(db, tenant, scope)
    if active:
        return active

    existing = (
        db.query(QuotationRoutingPolicy)
        .filter(
            QuotationRoutingPolicy.tenant_id == tenant,
            QuotationRoutingPolicy.route_scope == scope,
            QuotationRoutingPolicy.policy_name == DEFAULT_POLICY_NAME,
        )
        .order_by(QuotationRoutingPolicy.id.desc())
        .first()
    )
    if existing:
        existing.status = "published"
        existing.enabled = True
        existing.deleted = False
        existing.prompt_rules = _dump_json(_normalize_prompt_rules(DEFAULT_POLICY_RULES))
        existing.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
        existing.version_no = existing.version_no or 1
        existing.updater = operator
        existing.update_time = datetime.now()
        _disable_other_policies(db, existing)
        db.flush()
        return existing

    policy = create_policy(
        db,
        {
            "tenant_id": tenant,
            "policy_name": DEFAULT_POLICY_NAME,
            "status": "published",
            "enabled": True,
            "prompt_rules": DEFAULT_POLICY_RULES,
            "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
            "version_no": 1,
            "route_scope": scope,
            "remark": "系统初始化默认策略",
        },
        operator,
    )
    return policy


def serialize_policy(policy: QuotationRoutingPolicy) -> dict:
    return {
        "id": policy.id,
        "tenant_id": policy.tenant_id or "",
        "policy_name": policy.policy_name or "",
        "status": policy.status or "",
        "enabled": bool(policy.enabled),
        "prompt_rules": parse_prompt_rules(policy.prompt_rules),
        "confidence_threshold": _decimal_text(policy.confidence_threshold),
        "version_no": policy.version_no,
        "llm_model": policy.llm_model or "",
        "route_scope": policy.route_scope or DEFAULT_ROUTE_SCOPE,
        "remark": policy.remark or "",
        "creator": policy.creator or "",
        "create_time": policy.create_time.isoformat() if policy.create_time else None,
        "updater": policy.updater or "",
        "update_time": policy.update_time.isoformat() if policy.update_time else None,
        "deleted": bool(policy.deleted),
    }


def parse_prompt_rules(raw_value: str | dict | None) -> dict:
    return _normalize_prompt_rules(raw_value)


def _disable_other_policies(db: Session, current_policy: QuotationRoutingPolicy) -> None:
    db.query(QuotationRoutingPolicy).filter(
        QuotationRoutingPolicy.id != current_policy.id,
        QuotationRoutingPolicy.tenant_id == current_policy.tenant_id,
        QuotationRoutingPolicy.route_scope == current_policy.route_scope,
        QuotationRoutingPolicy.enabled == True,
        QuotationRoutingPolicy.deleted == False,
    ).update(
        {
            "enabled": False,
            "status": "disabled",
            "update_time": datetime.now(),
            "updater": current_policy.updater,
        },
        synchronize_session=False,
    )


def _normalize_prompt_rules(raw_value: str | dict | None) -> dict:
    data = raw_value
    if data in (None, ""):
        data = DEFAULT_PROMPT_RULES
    elif isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("prompt_rules 不是合法JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("prompt_rules 必须是对象")

    normalized = {}
    for key in DEFAULT_PROMPT_RULES:
        values = data.get(key, [])
        if values in (None, ""):
            values = []
        if not isinstance(values, list):
            raise ValueError(f"prompt_rules.{key} 必须是数组")
        normalized[key] = [str(item).strip() for item in values if str(item).strip()]
    return normalized


def _policy_status(value) -> str:
    status = str(value or "draft").strip().lower()
    if status not in {"draft", "published", "disabled"}:
        raise ValueError("策略状态必须是 draft/published/disabled")
    return status


def _route_scope(value) -> str:
    scope = str(value or DEFAULT_ROUTE_SCOPE).strip()
    if not scope:
        raise ValueError("route_scope 不能为空")
    return scope


def _required_text(value, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label}不能为空")
    return result


def _optional_text(value) -> str | None:
    result = str(value).strip() if value not in (None, "") else ""
    return result or None


def _positive_int(value, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if result <= 0:
        raise ValueError(f"{label}必须大于 0")
    return result


def _optional_probability(value, label: str):
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if result < 0 or result > 1:
        raise ValueError(f"{label}必须在 0 到 1 之间")
    return result


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"


def _dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
