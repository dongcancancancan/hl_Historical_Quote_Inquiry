import html
import json
from types import SimpleNamespace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.quotation import (
    QuotationBpmInstance,
    QuotationFieldOverride,
    QuotationMain,
    QuotationMaterial,
    QuotationProcessFee,
)
from app.models.calculation_trace import QuotationCalculationRun, QuotationCalculationTrace
from app.models.user import User
from app.database import SessionLocal
from app.services.bpm_instance_service import REVIEW_PARAM_FIELDS
from app.services.calc_param_service import normalize_vat_rate
from app.services.quotation_summary_service import apply_quotation_summaries
from app.services.internal_metric_service import apply_internal_metrics_to_instance, calculate_internal_metrics
from app.services.unit_price_override_service import (
    apply_unit_price_overrides,
    disable_unit_price_override,
    load_unit_price_overrides,
    upsert_unit_price_override,
)


MAIN_FIELDS = {
    "quotation_code": "text",
    "customer_name": "text",
    "customer_address": "text",
    "package_method": "text",
    "analysis_date": "date",
    "structure": "text",
    "braiding_rate": "percent",
    "product_spec": "text",
    "unit_usage_sum": "decimal",
    "material_amount_sum": "decimal",
    "material_cost": "decimal",
    "ul_label_fee": "decimal",
    "transport_fee": "decimal",
    "packing_fee": "decimal",
    "waste_loss_rate": "percent",
    "order_startup_times": "decimal",
    "total_fee": "decimal",
    "other_fee": "decimal",
    "irradiation_core_count": "decimal",
    "irradiation_core_fee": "decimal",
    "net_profit_rate": "percent",
    "customs_fee": "decimal",
    "vat_rate": "percent",
    "order_meterage": "decimal",
    "operating_expense_rate": "percent",
    "monthly_interest": "percent",
    "corporate_tax_rate": "percent",
    "cost": "decimal",
    "profit_selling_price": "decimal",
    "non_profit_price": "decimal",
    "final_selling_price": "decimal",
    "remark": "text",
}

MATERIAL_FIELDS = {
    "seq_no": "integer",
    "process_name": "text",
    "spec_detail": "text",
    "process_code": "text",
    "unit_usage": "decimal",
    "unit_price": "decimal",
    "material_amount": "decimal",
}

PROCESS_FIELDS = {
    "process_name": "text",
    "std_hours": "decimal",
    "loss_hours": "decimal",
    "fixed_rate": "decimal",
    "fixed_fee": "decimal",
    "startup_loss_wire": "decimal",
    "total_waste_glue": "decimal",
    "amount": "decimal",
    "subtotal_fee": "decimal",
}

REVIEW_PENDING = "pending"
REVIEW_QUOTED = "quoted"


def get_accessible_quotation(
    db: Session,
    quotation_code: str,
    tenant_id: str,
    creator_name: str,
    is_admin: bool = False,
    is_reviewer: bool = False,
) -> QuotationMain | None:
    from sqlalchemy import or_

    filters = [
        QuotationMain.quotation_code == quotation_code,
        QuotationMain.deleted == False,
    ]
    if not is_admin and not is_reviewer:
        filters.append(or_(QuotationMain.tenant_id == tenant_id, QuotationMain.tenant_id.is_(None)))
        admin_names = _admin_creator_names(db)
        if admin_names:
            filters.append(QuotationMain.creator.notin_(admin_names))
    quotation = db.query(QuotationMain).filter(*filters).first()
    if quotation and not is_reviewer and get_review_status(quotation) == REVIEW_QUOTED:
        return None
    return quotation


def _admin_creator_names(db: Session) -> list[str]:
    rows = db.query(User.username, User.display_name).filter(User.is_admin == True).all()
    names = set()
    for username, display_name in rows:
        if username:
            names.add(username)
        if display_name:
            names.add(display_name)
    return list(names)


def get_review_status(quotation: QuotationMain, instance: QuotationBpmInstance | None = None) -> str:
    if instance:
        return REVIEW_QUOTED if instance.review_status == REVIEW_QUOTED else REVIEW_PENDING
    return get_review_status_from_tags(quotation.extracted_tags)


def get_review_status_from_tags(raw_tags: str | None) -> str:
    tags = _load_tags(raw_tags)
    return REVIEW_QUOTED if tags.get("review_status") == REVIEW_QUOTED else REVIEW_PENDING


def set_review_status(
    db: Session,
    quotation: QuotationMain,
    status: str,
    updater: str,
    instance: QuotationBpmInstance | None = None,
) -> str:
    if status not in {REVIEW_PENDING, REVIEW_QUOTED}:
        raise ValueError("无效的报价状态")
    if instance:
        now = datetime.now()
        instance.review_status = status
        instance.quoted_time = now if status == REVIEW_QUOTED else None
        instance.cost = quotation.cost
        instance.profit_selling_price = quotation.profit_selling_price
        instance.non_profit_price = quotation.non_profit_price
        instance.final_selling_price = quotation.final_selling_price
        apply_internal_metrics_to_instance(instance, quotation)
        instance.updater = updater
        instance.update_time = now
        db.commit()
        return status
    tags = _load_tags(quotation.extracted_tags)
    tags["review_status"] = status
    quotation.extracted_tags = json.dumps(tags, ensure_ascii=False)
    quotation.updater = updater
    quotation.update_time = datetime.now()
    db.commit()
    return status


def get_review_history(db: Session, limit: int = 1000, search: str = "") -> dict[str, list[dict]]:
    search = (search or "").strip()
    filters = [
        QuotationMain.deleted == False,
        QuotationBpmInstance.deleted == False,
    ]
    if search:
        filters.append(or_(
            QuotationMain.quotation_code.contains(search),
            QuotationMain.bpm_no.contains(search),
            QuotationBpmInstance.bpm_no.contains(search),
            QuotationMain.customer_name.contains(search),
            QuotationMain.customer_address.contains(search),
            QuotationMain.package_method.contains(search),
            QuotationMain.product_spec.contains(search),
        ))

    rows = (
        db.query(QuotationMain, QuotationBpmInstance)
        .join(QuotationBpmInstance, QuotationBpmInstance.quotation_main_id == QuotationMain.id)
        .filter(*filters)
        .order_by(QuotationBpmInstance.upload_time.desc(), QuotationBpmInstance.id.desc())
        .limit(limit)
        .all()
    )
    history = {REVIEW_PENDING: [], REVIEW_QUOTED: []}
    instance_main_ids = set()
    for quotation, instance in rows:
        instance_main_ids.add(quotation.id)
        status = get_review_status(quotation, instance)
        history[status].append({
            "instance_id": instance.id,
            "quotation_code": quotation.quotation_code,
            "bpm_no": instance.bpm_no or quotation.bpm_no or "",
            "customer_name": quotation.customer_name or "",
            "package_method": getattr(quotation, "package_method", "") or "",
            "product_spec": quotation.product_spec or "",
            "upload_user": instance.upload_user or quotation.creator or "",
            "create_time": instance.upload_time.isoformat() if instance.upload_time else None,
            "quote_date": instance.quote_date.isoformat() if instance.quote_date else None,
            "review_status": status,
            "final_selling_price": str(instance.final_selling_price or quotation.final_selling_price or ""),
        })
    legacy_filters = [QuotationMain.deleted == False]
    if instance_main_ids:
        legacy_filters.append(QuotationMain.id.notin_(instance_main_ids))
    if search:
        legacy_filters.append(or_(
            QuotationMain.quotation_code.contains(search),
            QuotationMain.bpm_no.contains(search),
            QuotationMain.customer_name.contains(search),
            QuotationMain.customer_address.contains(search),
            QuotationMain.package_method.contains(search),
            QuotationMain.product_spec.contains(search),
        ))
    quotations = (
        db.query(QuotationMain)
        .filter(*legacy_filters)
        .order_by(QuotationMain.create_time.desc())
        .limit(limit)
        .all()
    )
    for quotation in quotations:
        status = get_review_status(quotation)
        history[status].append({
            "instance_id": None,
            "quotation_code": quotation.quotation_code,
            "bpm_no": quotation.bpm_no or "",
            "customer_name": quotation.customer_name or "",
            "package_method": getattr(quotation, "package_method", "") or "",
            "product_spec": quotation.product_spec or "",
            "upload_user": quotation.creator or "",
            "create_time": quotation.create_time.isoformat() if quotation.create_time else None,
            "quote_date": quotation.analysis_date.isoformat() if quotation.analysis_date else None,
            "review_status": status,
            "final_selling_price": str(quotation.final_selling_price or ""),
        })
    return history


def update_quotation_fields(
    db: Session,
    quotation: QuotationMain,
    changes: list[dict],
    updater: str,
    instance: QuotationBpmInstance | None = None,
) -> str:
    if get_review_status(quotation, instance) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能修改")
    now = datetime.now()
    materials = {item.id: item for item in quotation.materials if not item.deleted}
    processes = {item.id: item for item in quotation.processes if not item.deleted}
    instance_fields = {"analysis_date", "cost", "profit_selling_price", "non_profit_price", "final_selling_price"}

    for change in changes:
        entity = change.get("entity")
        record_id = change.get("id")
        field = change.get("field")
        raw_value = change.get("value")

        if entity == "main":
            target = quotation
            field_types = MAIN_FIELDS
            if record_id != quotation.id:
                raise ValueError("主表记录不属于当前成本分析表")
            if instance and field in instance_fields:
                field_type = field_types.get(field)
                if not field_type:
                    raise ValueError(f"字段 {field} 不允许修改")
                value = _parse_update_value(raw_value, field_type)
                if field == "analysis_date":
                    instance.quote_date = value
                else:
                    setattr(instance, field, value)
                instance.updater = updater
                instance.update_time = now
                continue
        elif entity == "material":
            target = materials.get(record_id)
            field_types = MATERIAL_FIELDS
            if not target:
                raise ValueError("材料明细不存在或不属于当前成本分析表")
        elif entity == "process":
            target = processes.get(record_id)
            field_types = PROCESS_FIELDS
            if not target:
                raise ValueError("制程费用明细不存在或不属于当前成本分析表")
        else:
            raise ValueError("不支持的数据区域")

        field_type = field_types.get(field)
        if not field_type:
            raise ValueError(f"字段 {field} 不允许修改")
        if entity == "material" and field == "unit_price":
            if raw_value is None or str(raw_value).strip() == "":
                disable_unit_price_override(db, quotation.id, record_id, updater)
                target.unit_price = None
                target.material_amount = None
            else:
                value = _parse_update_value(raw_value, field_type)
                if value <= 0:
                    disable_unit_price_override(db, quotation.id, record_id, updater)
                    target.unit_price = None
                    target.material_amount = None
                else:
                    upsert_unit_price_override(db, quotation, record_id, value, target.unit_price, updater)
            target.updater = updater
            target.update_time = now
            continue
        value = _parse_update_value(raw_value, field_type)

        if entity == "main" and field == "quotation_code":
            if not value:
                raise ValueError("成本分析号不能为空")
            duplicate = db.query(QuotationMain).filter(
                QuotationMain.tenant_id == quotation.tenant_id,
                QuotationMain.quotation_code == value,
                QuotationMain.id != quotation.id,
            ).first()
            if duplicate:
                raise ValueError("成本分析号已存在，请使用其他编号")

        setattr(target, field, value)
        if entity == "main" and instance and field in REVIEW_PARAM_FIELDS:
            setattr(instance, field, value)
        if entity != "main":
            target.updater = updater
            target.update_time = now

    apply_quotation_summaries(quotation, materials.values(), processes.values(), updater, now)
    if instance:
        instance.quotation_code = quotation.quotation_code or instance.quotation_code
        instance.updater = updater
        instance.update_time = now
    db.commit()
    return quotation.quotation_code


def clear_material_unit_prices(
    db: Session,
    quotation: QuotationMain,
    updater: str,
    instance: QuotationBpmInstance | None = None,
) -> dict:
    if get_review_status(quotation, instance) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能修改")

    now = datetime.now()
    materials = [item for item in quotation.materials if not item.deleted]
    processes = [item for item in quotation.processes if not item.deleted]
    disabled_overrides = (
        db.query(QuotationFieldOverride)
        .filter(
            QuotationFieldOverride.quotation_main_id == quotation.id,
            QuotationFieldOverride.entity_type == "material",
            QuotationFieldOverride.field_name == "unit_price",
            QuotationFieldOverride.enabled == True,
        )
        .update(
            {
                "enabled": False,
                "updater": updater,
                "update_time": now,
            },
            synchronize_session=False,
        )
    )

    cleared_materials = 0
    for item in materials:
        if item.unit_price is not None or item.material_amount is not None:
            cleared_materials += 1
        item.unit_price = None
        item.material_amount = None
        item.updater = updater
        item.update_time = now

    cleared_processes = 0
    for item in processes:
        if item.amount is not None or item.subtotal_fee is not None:
            cleared_processes += 1
        item.amount = None
        item.subtotal_fee = None
        item.updater = updater
        item.update_time = now

    quotation.unit_usage_sum = None
    quotation.material_amount_sum = None
    quotation.material_cost = None
    quotation.total_fee = None
    quotation.cost = None
    quotation.profit_selling_price = None
    quotation.non_profit_price = None
    quotation.final_selling_price = None
    quotation.updater = updater
    quotation.update_time = now

    if instance:
        instance.cost = None
        instance.profit_selling_price = None
        instance.non_profit_price = None
        instance.final_selling_price = None
        instance.material_ratio = None
        instance.order_weight = None
        instance.updater = updater
        instance.update_time = now

    cleared_traces = (
        db.query(QuotationCalculationTrace)
        .filter(
            QuotationCalculationTrace.quotation_main_id == quotation.id,
            QuotationCalculationTrace.run_id.is_(None),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "quotation_code": quotation.quotation_code or "",
        "cleared": cleared_materials,
        "cleared_materials": cleared_materials,
        "cleared_processes": cleared_processes,
        "cleared_traces": cleared_traces,
        "disabled_overrides": disabled_overrides,
    }


def _load_tags(raw_tags: str | None) -> dict:
    try:
        tags = json.loads(raw_tags or "{}")
        return tags if isinstance(tags, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _parse_update_value(raw_value, field_type: str):
    value = "" if raw_value is None else str(raw_value).strip()
    if field_type == "text":
        return value
    if field_type == "date":
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期格式必须为 YYYY-MM-DD") from exc
    if not value:
        return None
    if field_type == "percent":
        value = value.removesuffix("%").strip()
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{raw_value} 不是有效数字") from exc
    if field_type == "percent":
        return number / Decimal("100")
    if field_type == "integer":
        return int(number)
    return number


def render_quotation_preview(
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None = None,
    apply_unit_price_override_values: bool = True,
    run_id: int | None = None,
) -> str:
    """Render a cost-analysis worksheet from structured database fields only."""
    unit_price_overrides = {}
    if apply_unit_price_override_values:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            unit_price_overrides = load_unit_price_overrides(db, quotation.id)
        finally:
            db.close()
        apply_unit_price_overrides(quotation, unit_price_overrides)
    apply_quotation_summaries(quotation)
    materials = sorted(
        (item for item in quotation.materials if not item.deleted),
        key=lambda item: (item.seq_no or 0, item.id or 0),
    )
    processes = sorted(
        (item for item in quotation.processes if not item.deleted),
        key=lambda item: item.id or 0,
    )
    unit_usage_sum = quotation.unit_usage_sum
    if unit_usage_sum is None:
        unit_usage_sum = sum((_decimal(item.unit_usage) for item in materials), Decimal("0")) / Decimal("100")
    display_analysis_date = instance.quote_date if instance and instance.quote_date else quotation.analysis_date
    display_cost = instance.cost if instance and instance.cost is not None else quotation.cost
    display_profit_price = (
        instance.profit_selling_price
        if instance and instance.profit_selling_price is not None
        else quotation.profit_selling_price
    )
    display_non_profit_price = (
        instance.non_profit_price
        if instance and instance.non_profit_price is not None
        else quotation.non_profit_price
    )
    display_final_price = (
        instance.final_selling_price
        if instance and instance.final_selling_price is not None
        else quotation.final_selling_price
    )
    raw_display_vat_rate = (instance.vat_rate if instance and instance.vat_rate is not None else quotation.vat_rate)
    try:
        display_vat_rate = normalize_vat_rate(raw_display_vat_rate) if raw_display_vat_rate is not None else None
    except (ValueError, Exception):
        display_vat_rate = None
    internal_metrics_html = _render_internal_metrics_panel(quotation, instance)

    material_rows = "".join(
        _row(
            _edit_cell(item.seq_no, "material", item.id, "seq_no"),
            _edit_cell(item.process_name, "material", item.id, "process_name"),
            _edit_cell(item.spec_detail, "material", item.id, "spec_detail", colspan=3, css="left"),
            _edit_cell(item.process_code, "material", item.id, "process_code"),
            _edit_cell(item.unit_usage, "material", item.id, "unit_usage", css="number"),
            _edit_cell(item.unit_price, "material", item.id, "unit_price", css="number", locked=item.id in unit_price_overrides),
            _edit_cell(item.material_amount, "material", item.id, "material_amount", css="number"),
        )
        for item in materials
    )
    if not material_rows:
        material_rows = _row(_cell("暂无材料明细", colspan=9, css="muted"))

    process_rows = "".join(
        _row(
            _edit_cell(item.process_name, "process", item.id, "process_name"),
            _edit_cell(item.std_hours, "process", item.id, "std_hours", css="number"),
            _edit_cell(item.loss_hours, "process", item.id, "loss_hours", css="number"),
            _edit_cell(item.fixed_rate, "process", item.id, "fixed_rate", css="number"),
            _edit_cell(item.fixed_fee, "process", item.id, "fixed_fee", css="number"),
            _edit_cell(item.startup_loss_wire, "process", item.id, "startup_loss_wire", css="number"),
            _edit_cell(item.total_waste_glue, "process", item.id, "total_waste_glue", css="number"),
            _edit_cell(item.amount, "process", item.id, "amount", css="number"),
            _edit_cell(item.subtotal_fee, "process", item.id, "subtotal_fee", css="number"),
        )
        for item in processes
    )
    if not process_rows:
        process_rows = _row(_cell("暂无制程费用明细", colspan=9, css="muted"))

    table_html = "".join(
        [
            _row(_cell("成 本 分 析 表", colspan=9, css="title")),
            _row(
                _cell("", colspan=4),
                _cell("包装方式-米数:", css="label"),
                _edit_cell(getattr(quotation, "package_method", "") or "", "main", quotation.id, "package_method", colspan=2, css="value left"),
                _cell("编号:", css="label"),
                _edit_cell(quotation.quotation_code, "main", quotation.id, "quotation_code", css="value"),
            ),
            _row(
                _cell("客户名称:", css="label"),
                _edit_cell(quotation.customer_name, "main", quotation.id, "customer_name", colspan=3, css="value left"),
                _cell("收货地（市）:", css="label"),
                _edit_cell(quotation.customer_address, "main", quotation.id, "customer_address", css="value left"),
                _cell("分析日期:", css="label"),
                _edit_cell(display_analysis_date, "main", quotation.id, "analysis_date", colspan=2, css="value"),
            ),
            _row(
                _cell("结构:", css="label"),
                _edit_cell(quotation.structure, "main", quotation.id, "structure", colspan=3, css="value left"),
                _cell("编织率(%):", css="label"),
                _edit_cell(_percent(quotation.braiding_rate, suffix=False), "main", quotation.id, "braiding_rate", css="value number", scale="percent"),
                _cell("品名规格:", css="label"),
                _edit_cell(quotation.product_spec, "main", quotation.id, "product_spec", colspan=2, css="value left"),
            ),
            _row(
                _cell("序号", rowspan=2, css="header"),
                _cell("制程", rowspan=2, css="header"),
                _cell("规格", colspan=3, rowspan=2, css="header"),
                _cell("物料编码", rowspan=2, css="header"),
                _cell("单位用量", css="header"),
                _cell("单价", css="header"),
                _cell("材料金额", css="header"),
            ),
            _row(
                _cell("KG/100M", css="unit"),
                _cell("RMB/KG", css="unit"),
                _cell("RMB/100M", css="unit"),
            ),
            material_rows,
            _row(
                _cell("材料成本", colspan=2, css="summary"),
                _edit_cell(quotation.material_cost, "main", quotation.id, "material_cost", css="number summary"),
                _cell("RMB/M", colspan=3, css="unit"),
                _edit_cell(unit_usage_sum, "main", quotation.id, "unit_usage_sum", css="number summary"),
                _cell("KG/M", css="unit"),
                _edit_cell(quotation.material_amount_sum, "main", quotation.id, "material_amount_sum", css="number summary"),
            ),
            _row(
                _cell("制程", css="header"),
                _cell("标准工时(一台机1KM开机时间)", css="header"),
                _cell("损耗时间(1KM)", css="header"),
                _cell("固定费用率", css="header"),
                _cell("固定费用", css="header"),
                _cell("开机损耗废线", css="header"),
                _cell("每个制程总废胶(KG)", css="header"),
                _cell("金额", css="header"),
                _cell("费用成本小计", css="header"),
            ),
            process_rows,
            _row(
                _cell("其他费用", colspan=2, rowspan=6, css="fee-title"),
                _cell("UL标签费(RMB/M)", css="fee-label"),
                _cell("运输费(RMB/KG)", css="fee-label"),
                _cell("包装费(RMB/M)", css="fee-label"),
                _cell("废品损耗(%)", css="fee-label alert"),
                _cell("订单开机次数", css="fee-label"),
                _cell("费用总计", css="fee-label"),
                _edit_cell(quotation.total_fee, "main", quotation.id, "total_fee", css="fee-total number alert"),
            ),
            _row(
                _edit_cell(quotation.ul_label_fee, "main", quotation.id, "ul_label_fee", css="fee-value number"),
                _edit_cell(quotation.transport_fee, "main", quotation.id, "transport_fee", css="fee-value number"),
                _edit_cell(quotation.packing_fee, "main", quotation.id, "packing_fee", css="fee-value number"),
                _edit_cell(_percent(quotation.waste_loss_rate), "main", quotation.id, "waste_loss_rate", css="fee-value number", scale="percent"),
                _edit_cell(quotation.order_startup_times, "main", quotation.id, "order_startup_times", css="fee-value number"),
                _cell("成本(RMB/M)", css="price-label"),
                _cell("取利售价(RMB/M)", css="price-label"),
            ),
            _row(
                _cell("其他费用(运货费)", css="fee-label"),
                _cell("净利率", css="fee-label"),
                _cell("报关费(RMB/次)", css="fee-label"),
                _cell("增值税率", css="fee-label"),
                _cell("订单米数", css="fee-label"),
                _edit_cell(display_cost, "main", quotation.id, "cost", css="price-value number alert"),
                _edit_cell(display_profit_price, "main", quotation.id, "profit_selling_price", css="price-value featured number"),
            ),
            _row(
                _edit_cell(quotation.other_fee, "main", quotation.id, "other_fee", css="fee-value number"),
                _edit_cell(_percent(quotation.net_profit_rate), "main", quotation.id, "net_profit_rate", css="fee-value number", scale="percent"),
                _edit_cell(quotation.customs_fee, "main", quotation.id, "customs_fee", css="fee-value number"),
                _edit_cell(_percent(display_vat_rate), "main", quotation.id, "vat_rate", css="fee-value number", scale="percent"),
                _edit_cell(quotation.order_meterage, "main", quotation.id, "order_meterage", css="fee-value number"),
                _cell("不取利售价(RMB/M)", css="price-label"),
                _cell("最终售价(RMB/M)", css="price-label"),
            ),
            _row(
                _cell("照射芯数", css="fee-label alert"),
                _cell("照射费用(RMB/M)", css="fee-label alert"),
                _cell("营业费用率", css="fee-label"),
                _cell("月结利息", css="fee-label"),
                _cell("企税税率", css="fee-label"),
                _edit_cell(display_non_profit_price, "main", quotation.id, "non_profit_price", css="price-value number alert"),
                _edit_cell(display_final_price, "main", quotation.id, "final_selling_price", css="price-value featured number"),
            ),
            _row(
                _edit_cell(quotation.irradiation_core_count, "main", quotation.id, "irradiation_core_count", css="fee-value number"),
                _edit_cell(quotation.irradiation_core_fee, "main", quotation.id, "irradiation_core_fee", css="fee-value number"),
                _edit_cell(_percent(quotation.operating_expense_rate), "main", quotation.id, "operating_expense_rate", css="fee-value number", scale="percent"),
                _edit_cell(_percent(quotation.monthly_interest), "main", quotation.id, "monthly_interest", css="fee-value number", scale="percent"),
                _edit_cell(_percent(quotation.corporate_tax_rate), "main", quotation.id, "corporate_tax_rate", css="fee-value number", scale="percent"),
                _cell("", colspan=2, css="price-value"),
            ),
            _row(
                _cell("备注:", css="label"),
                _edit_cell(quotation.remark, "main", quotation.id, "remark", colspan=8, css="value left"),
            ),
        ]
    )
    traces = _build_trace_tooltips(
        quotation.id,
        bpm_instance_id=instance.id if instance else None,
        run_id=run_id,
    )
    trace_json = json.dumps(traces, ensure_ascii=False)
    return _wrap_preview_html(quotation.quotation_code or "", table_html, internal_metrics_html, trace_json)


def render_quote_snapshot_preview(snapshot_data: dict) -> str:
    main = snapshot_data.get("main") or {}
    materials = [
        SimpleNamespace(**{**item, "deleted": False})
        for item in (snapshot_data.get("materials") or [])
    ]
    processes = [
        SimpleNamespace(**{**item, "deleted": False})
        for item in (snapshot_data.get("processes") or [])
    ]
    meta = snapshot_data.get("meta") or {}
    snapshot_run_id = meta.get("calculation_run_id")
    quotation = SimpleNamespace(
        **main,
        materials=materials,
        processes=processes,
        deleted=False,
        updater="",
        update_time=None,
    )
    return render_quotation_preview(quotation, None, apply_unit_price_override_values=False, run_id=snapshot_run_id)


def _row(*cells: str) -> str:
    return f"<tr>{''.join(cells)}</tr>"


def _cell(value="", colspan: int = 1, rowspan: int = 1, css: str = "") -> str:
    span = f' colspan="{colspan}"' if colspan > 1 else ""
    span += f' rowspan="{rowspan}"' if rowspan > 1 else ""
    classes = f' class="{css}"' if css else ""
    return f"<td{span}{classes}>{html.escape(_format_value(value))}</td>"


def _edit_cell(value, entity: str, record_id: int, field: str, colspan: int = 1, css: str = "", scale: str = "", locked: bool = False) -> str:
    span = f' colspan="{colspan}"' if colspan > 1 else ""
    css = f"{css} locked-cell".strip() if locked else css
    classes = f' class="{css}"' if css else ""
    locked_flag = "1" if locked else "0"
    attrs = (
        f'data-entity="{entity}" data-id="{record_id}" data-field="{field}" '
        f'data-scale="{scale}" data-locked="{locked_flag}" value="{html.escape(_format_value(value), quote=True)}"'
    )
    marker = '<span class="lock-mark" title="手工单价">手</span>' if locked else ""
    return f'<td{span}{classes}><div class="cell-wrap"><input class="sheet-input" {attrs} disabled>{marker}</div></td>'


def _format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, float)):
        text = f"{value:f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _percent(value, suffix: bool = True) -> str:
    result = _format_value(_decimal(value) * Decimal("100"))
    return f"{result}%" if suffix else result


def _render_internal_metrics_panel(quotation, instance: QuotationBpmInstance | None = None) -> str:
    metrics = calculate_internal_metrics(quotation, instance)
    material_ratio = (
        getattr(instance, "material_ratio", None)
        if instance and getattr(instance, "material_ratio", None) is not None
        else getattr(quotation, "material_ratio", None)
    )
    order_weight = (
        getattr(instance, "order_weight", None)
        if instance and getattr(instance, "order_weight", None) is not None
        else getattr(quotation, "order_weight", None)
    )
    if material_ratio is None:
        material_ratio = metrics["material_ratio"]
    if order_weight is None:
        order_weight = metrics["order_weight"]
    material_cost = getattr(quotation, "material_cost", None)
    final_selling_price = (
        getattr(instance, "final_selling_price", None)
        if instance and getattr(instance, "final_selling_price", None) is not None
        else getattr(quotation, "final_selling_price", None)
    )
    vat_rate = (
        getattr(instance, "vat_rate", None)
        if instance and getattr(instance, "vat_rate", None) is not None
        else getattr(quotation, "vat_rate", None)
    )
    unit_usage_sum = getattr(quotation, "unit_usage_sum", None)
    order_meterage = (
        getattr(instance, "order_meterage", None)
        if instance and getattr(instance, "order_meterage", None) is not None
        else getattr(quotation, "order_meterage", None)
    )
    normalized_vat_rate = normalize_vat_rate(vat_rate) if vat_rate is not None else Decimal("0")
    tax_formula = "材料占比 = 材料成本 × (1 + 增值税率) / 最终售价"
    tax_steps = (
        f"材料成本 = {_format_value(material_cost) or '-'}\n"
        f"增值税率 = {_format_value(normalized_vat_rate) or '0'}\n"
        f"最终售价 = {_format_value(final_selling_price) or '-'}\n"
        f"计算结果 = {_format_percent_value(material_ratio)}"
    )
    weight_formula = "订单重量 = 单位用量合计(KG/M) × 订单米数"
    weight_steps = (
        f"单位用量合计 = {_format_value(unit_usage_sum) or '-'} KG/M\n"
        f"订单米数 = {_format_value(order_meterage) or '-'}\n"
        f"计算结果 = {_format_weight_value(order_weight)}"
    )
    return f"""
    <section class="internal-metrics" aria-label="内部指标">
        <div class="internal-metric" data-formula="{html.escape(tax_formula, quote=True)}" data-steps="{html.escape(tax_steps, quote=True)}">
            <span>材料占比</span>
            <strong>{html.escape(_format_percent_value(material_ratio))}</strong>
        </div>
        <div class="internal-metric" data-formula="{html.escape(weight_formula, quote=True)}" data-steps="{html.escape(weight_steps, quote=True)}">
            <span>订单重量</span>
            <strong>{html.escape(_format_weight_value(order_weight))}</strong>
        </div>
    </section>
    """


def _format_percent_value(value) -> str:
    if value in (None, ""):
        return "-"
    return f"{_format_value(_decimal(value) * Decimal('100'))}%"


def _format_weight_value(value) -> str:
    if value in (None, ""):
        return "-"
    return f"{_format_value(value)} KG"


def _build_trace_tooltips(quotation_main_id: int, bpm_instance_id: int | None = None, run_id: int | None = None) -> dict[str, dict[str, str]]:
    """查询计算追踪记录，按 key 索引用于前端 tooltip 展示。"""
    db = SessionLocal()
    try:
        if run_id:
            run_filter = (QuotationCalculationTrace.run_id == run_id)
        else:
            run = _latest_calc_run(db, quotation_main_id, bpm_instance_id)
            run_filter = (QuotationCalculationTrace.run_id == run.id) if run else None
        if run_filter is None:
            return {}
        rows = (
            db.query(QuotationCalculationTrace)
            .filter(
                QuotationCalculationTrace.quotation_main_id == quotation_main_id,
                run_filter,
            )
            .order_by(QuotationCalculationTrace.id.desc())
            .limit(2000)
            .all()
        )
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            entity = row.entity_type
            entity_id = row.entity_id
            input_data = _load_json(row.input_data)
            if not entity:
                process_fee_id = input_data.get("process_fee_id")
                if row.field_name in {"process_amount", "process_subtotal_fee"} and process_fee_id:
                    entity = "process"
                    entity_id = _int_or_none(process_fee_id)
                else:
                    # 兼容旧数据：无 entity_type 时从 material_id 推断
                    entity = "material" if row.material_id else "main"
            if not entity_id:
                entity_id = row.material_id or quotation_main_id
            field = row.field_name or ""
            steps = (row.process_text or "").strip()
            tooltip = {
                "f": (row.formula or "").strip(),
                "p": steps,
            }
            for key in _trace_tooltip_keys(entity, entity_id, field):
                if key in result:
                    continue
                result[key] = tooltip
        return result
    finally:
        db.close()


def _trace_tooltip_keys(entity: str, entity_id: int, field: str) -> list[str]:
    keys = [f"{entity}:{entity_id}:{field}"]
    if entity == "process":
        aliases = {
            "process_amount": "amount",
            "process_subtotal_fee": "subtotal_fee",
        }
        alias = aliases.get(field)
        if alias:
            keys.append(f"{entity}:{entity_id}:{alias}")
    return keys


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_json(raw_value: str | None) -> dict:
    if not raw_value:
        return {}
    try:
        data = json.loads(raw_value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _latest_calc_run(db: Session, quotation_main_id: int, bpm_instance_id: int | None = None) -> QuotationCalculationRun | None:
    query = db.query(QuotationCalculationRun).filter(
        QuotationCalculationRun.quotation_main_id == quotation_main_id,
        QuotationCalculationRun.status == "success",
    )
    if bpm_instance_id:
        run = query.filter(QuotationCalculationRun.bpm_instance_id == bpm_instance_id).order_by(QuotationCalculationRun.id.desc()).first()
        if run:
            return run
    return (
        db.query(QuotationCalculationRun)
        .filter(
            QuotationCalculationRun.quotation_main_id == quotation_main_id,
            QuotationCalculationRun.status == "success",
        )
        .order_by(QuotationCalculationRun.id.desc())
        .first()
    )


def _wrap_preview_html(quotation_code: str, table_html: str, internal_metrics_html: str = "", trace_json: str = "{}") -> str:
    trace_count = len(_load_json(trace_json))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(quotation_code)}</title>
    <style>
        body {{ margin: 0; padding: 16px; color: #111827; background: #f8fafc; font-family: Arial, "Microsoft YaHei", sans-serif; }}
        .meta {{ margin-bottom: 12px; color: #64748b; font-size: 12px; }}
        .sheet {{ min-width: 1080px; box-sizing: border-box; background: #fff; border: 1px solid #111827; }}
        table {{ width: 100%; border-collapse: collapse; table-layout: fixed; background: #fff; }}
        col:nth-child(1) {{ width: 7%; }}
        col:nth-child(2) {{ width: 8%; }}
        col:nth-child(3) {{ width: 12%; }}
        col:nth-child(4) {{ width: 11%; }}
        col:nth-child(5) {{ width: 11%; }}
        col:nth-child(6) {{ width: 12%; }}
        col:nth-child(7) {{ width: 12%; }}
        col:nth-child(8) {{ width: 13%; }}
        col:nth-child(9) {{ width: 14%; }}
        td {{ min-width: 54px; padding: 3px 5px; border: 1px solid #111827; line-height: 1.2; text-align: center; vertical-align: middle; overflow-wrap: anywhere; }}
        .title {{ height: 34px; background: #fff900; color: #000; font-family: SimSun, serif; font-size: 25px; font-weight: 400; letter-spacing: 12px; }}
        .header, .label, .section, .summary {{ font-weight: 700; }}
        .header {{ background: #f3f4f6; }}
        .section {{ background: #e5e7eb; }}
        .summary {{ background: #fef3c7; }}
        .unit, .muted {{ color: #6b7280; font-size: 12px; }}
        .left {{ text-align: left; }}
        .number {{ font-family: Consolas, monospace; }}
        .fee-title, .fee-label {{ background: #c6ffc6; }}
        .fee-title {{ font-size: 16px; font-weight: 700; }}
        .fee-value {{ background: #f6a8d0; }}
        .fee-total, .price-label, .price-value {{ background: #fff900; }}
        .price-label {{ font-weight: 700; }}
        .featured {{ color: #0000d7; font-size: 17px; font-weight: 700; }}
        .alert {{ color: #ef0000; }}
        .sheet-input {{ width: 100%; box-sizing: border-box; padding: 1px 2px; border: 1px solid transparent; outline: none; background: transparent; color: inherit; font: inherit; text-align: inherit; }}
        .sheet-input:not(:disabled) {{ border-color: #2563eb; background: #fff; }}
        .sheet-input.has-trace {{ cursor: help; }}
        .sheet-input.has-trace:hover {{ background: #fef9c3 !important; }}
        td.has-trace {{ cursor: help; }}
        td.has-trace:hover .sheet-input {{ background: #fef9c3 !important; }}
        .cell-wrap {{ position: relative; display: flex; align-items: center; }}
        .locked-cell {{ background: #dbeafe !important; }}
        .lock-mark {{ position: absolute; right: 2px; top: 1px; color: #1d4ed8; font-size: 10px; font-family: Arial, sans-serif; }}
        .internal-metrics {{ min-width: 1080px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }}
        .internal-metric {{ border: 1px solid #cbd5e1; background: #fff; padding: 12px 14px; box-shadow: 0 1px 2px rgb(15 23 42 / 4%); cursor: help; }}
        .internal-metric:hover {{ background: #fefce8; border-color: #f59e0b; }}
        .internal-metric span {{ display: block; color: #64748b; font-size: 12px; }}
        .internal-metric strong {{ display: block; margin-top: 6px; color: #111827; font-family: Consolas, monospace; font-size: 20px; }}
        /* tooltip */
        .trace-tooltip {{ position: fixed; z-index: 99999; max-width: 480px; padding: 12px 14px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; box-shadow: 0 10px 25px rgb(15 23 42 / 15%); font-size: 12px; line-height: 1.6; pointer-events: none; opacity: 0; transition: opacity 0.15s; }}
        .trace-tooltip.visible {{ opacity: 1; }}
        .trace-tooltip .tt-formula {{ display: block; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #eef2f7; color: #111827; font-family: Consolas, monospace; font-size: 13px; font-weight: 600; }}
        .trace-tooltip .tt-steps {{ white-space: pre-wrap; color: #374151; }}
    </style>
</head>
<body data-trace-count="{trace_count}">
    <div class="meta">数据库实时渲染 · 成本分析号：{html.escape(quotation_code)}</div>
    <div class="sheet"><table><colgroup><col><col><col><col><col><col><col><col><col></colgroup><tbody>{table_html}</tbody></table></div>
    {internal_metrics_html}
    <div class="trace-tooltip" id="traceTooltip"><span class="tt-formula"></span><span class="tt-steps"></span></div>
    <script>
    (function() {{
        var traces = {trace_json};
        var tooltip = document.getElementById('traceTooltip');
        var ff = tooltip.querySelector('.tt-formula');
        var ss = tooltip.querySelector('.tt-steps');
        var currentKey = null;

        function show(ev, key) {{
            if (currentKey === key) return;
            currentKey = key;
            var t = traces[key];
            if (!t) {{ ff.textContent = '(无计算追踪)'; ss.textContent = ''; }}
            else {{ ff.textContent = t.f || ''; ss.textContent = t.p || ''; }}
            tooltip.classList.add('visible');
            move(ev);
        }}

        function showMetric(ev, el) {{
            currentKey = null;
            ff.textContent = el.dataset.formula || '';
            ss.textContent = el.dataset.steps || '';
            tooltip.classList.add('visible');
            move(ev);
        }}

        function move(ev) {{
            var x = ev.clientX + 16, y = ev.clientY - 8;
            if (x + 490 > window.innerWidth) x = ev.clientX - 500;
            if (y + 200 > window.innerHeight) y = ev.clientY - 210;
            tooltip.style.left = x + 'px';
            tooltip.style.top = y + 'px';
        }}

        function hide() {{
            currentKey = null;
            tooltip.classList.remove('visible');
        }}

        document.querySelectorAll('.sheet-input').forEach(function(el) {{
            var key = (el.dataset.entity || '') + ':' + (el.dataset.id || '') + ':' + (el.dataset.field || '');
            if (!traces[key]) return;
            var hoverTarget = el.closest('td') || el.parentElement || el;
            el.classList.add('has-trace');
            hoverTarget.classList.add('has-trace');
            hoverTarget.addEventListener('mouseenter', function(ev) {{ show(ev, key); }});
            hoverTarget.addEventListener('mousemove', move);
            hoverTarget.addEventListener('mouseleave', hide);
        }});
        document.querySelectorAll('.internal-metric[data-formula]').forEach(function(el) {{
            el.addEventListener('mouseenter', function(ev) {{ showMetric(ev, el); }});
            el.addEventListener('mousemove', move);
            el.addEventListener('mouseleave', hide);
        }});
    }})();
    </script>
</body>
</html>"""
