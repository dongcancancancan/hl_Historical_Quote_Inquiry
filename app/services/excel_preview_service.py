import html
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.quotation import QuotationFieldOverride, QuotationMain, QuotationMaterial, QuotationProcessFee
from app.models.user import User
from app.services.quotation_summary_service import apply_quotation_summaries
from app.services.unit_price_override_service import (
    apply_unit_price_overrides,
    disable_unit_price_override,
    load_unit_price_overrides,
    upsert_unit_price_override,
)
from app.services.bpm_lookup_service import (
    build_quotation_code_filter,
    get_bpm_flows_by_quotation_codes,
    get_quotation_codes_by_bpm,
    resolve_bpm_no,
)


MAIN_FIELDS = {
    "quotation_code": "text",
    "customer_name": "text",
    "customer_address": "text",
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


def get_review_status(quotation: QuotationMain) -> str:
    return get_review_status_from_tags(quotation.extracted_tags)


def get_review_status_from_tags(raw_tags: str | None) -> str:
    tags = _load_tags(raw_tags)
    return REVIEW_QUOTED if tags.get("review_status") == REVIEW_QUOTED else REVIEW_PENDING


def set_review_status(db: Session, quotation: QuotationMain, status: str, updater: str) -> str:
    if status not in {REVIEW_PENDING, REVIEW_QUOTED}:
        raise ValueError("无效的报价状态")
    tags = _load_tags(quotation.extracted_tags)
    tags["review_status"] = status
    quotation.extracted_tags = json.dumps(tags, ensure_ascii=False)
    quotation.updater = updater
    quotation.update_time = datetime.now()
    db.commit()
    return status


def get_review_history(db: Session, limit: int = 1000, search: str = "") -> dict[str, list[dict]]:
    search = (search or "").strip()
    filters = [QuotationMain.deleted == False]
    if search:
        workflow_codes = get_quotation_codes_by_bpm(db, search)
        if workflow_codes:
            filters.append(build_quotation_code_filter(QuotationMain.quotation_code, workflow_codes))
        else:
            filters.append(QuotationMain.quotation_code.contains(search))

    quotations = (
        db.query(QuotationMain)
        .filter(*filters)
        .order_by(QuotationMain.create_time.desc())
        .limit(limit)
        .all()
    )
    bpm_map = get_bpm_flows_by_quotation_codes(
        db,
        [quotation.quotation_code for quotation in quotations if quotation.quotation_code],
    )
    history = {REVIEW_PENDING: [], REVIEW_QUOTED: []}
    for quotation in quotations:
        status = get_review_status(quotation)
        history[status].append({
            "quotation_code": quotation.quotation_code,
            "bpm_no": resolve_bpm_no(bpm_map, quotation.quotation_code, quotation.bpm_no),
            "customer_name": quotation.customer_name or "",
            "product_spec": quotation.product_spec or "",
            "upload_user": quotation.creator or "",
            "create_time": quotation.create_time.isoformat() if quotation.create_time else None,
            "review_status": status,
        })
    return history


def update_quotation_fields(
    db: Session,
    quotation: QuotationMain,
    changes: list[dict],
    updater: str,
) -> str:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能修改")
    now = datetime.now()
    materials = {item.id: item for item in quotation.materials if not item.deleted}
    processes = {item.id: item for item in quotation.processes if not item.deleted}

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
        if entity != "main":
            target.updater = updater
            target.update_time = now

    apply_quotation_summaries(quotation, materials.values(), processes.values(), updater, now)
    db.commit()
    return quotation.quotation_code


def clear_material_unit_prices(db: Session, quotation: QuotationMain, updater: str) -> dict:
    if get_review_status(quotation) == REVIEW_QUOTED:
        raise ValueError("该成本分析表已报价，只能查看，不能修改")

    now = datetime.now()
    materials = [item for item in quotation.materials if not item.deleted]
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

    cleared = 0
    for item in materials:
        if item.unit_price is not None or item.material_amount is not None:
            cleared += 1
        item.unit_price = None
        item.material_amount = None
        item.updater = updater
        item.update_time = now

    apply_quotation_summaries(quotation, materials, quotation.processes, updater, now)
    db.commit()
    return {
        "quotation_code": quotation.quotation_code or "",
        "cleared": cleared,
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


def render_quotation_preview(quotation: QuotationMain) -> str:
    """Render a cost-analysis worksheet from structured database fields only."""
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
                _cell("", colspan=7),
                _cell("编号:", css="label"),
                _edit_cell(quotation.quotation_code, "main", quotation.id, "quotation_code", css="value"),
            ),
            _row(
                _cell("客户:", css="label"),
                _edit_cell(quotation.customer_name, "main", quotation.id, "customer_name", colspan=3, css="value left"),
                _cell("地址:", css="label"),
                _edit_cell(quotation.customer_address, "main", quotation.id, "customer_address", css="value left"),
                _cell("分析日期:", css="label"),
                _edit_cell(quotation.analysis_date, "main", quotation.id, "analysis_date", colspan=2, css="value"),
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
                _cell("其他费用", css="fee-label"),
                _cell("净利率", css="fee-label"),
                _cell("报关费(RMB/次)", css="fee-label"),
                _cell("增值税率", css="fee-label"),
                _cell("订单米数", css="fee-label"),
                _edit_cell(quotation.cost, "main", quotation.id, "cost", css="price-value number alert"),
                _edit_cell(quotation.profit_selling_price, "main", quotation.id, "profit_selling_price", css="price-value featured number"),
            ),
            _row(
                _edit_cell(quotation.other_fee, "main", quotation.id, "other_fee", css="fee-value number"),
                _edit_cell(_percent(quotation.net_profit_rate), "main", quotation.id, "net_profit_rate", css="fee-value number", scale="percent"),
                _edit_cell(quotation.customs_fee, "main", quotation.id, "customs_fee", css="fee-value number"),
                _edit_cell(_percent(quotation.vat_rate), "main", quotation.id, "vat_rate", css="fee-value number", scale="percent"),
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
                _edit_cell(quotation.non_profit_price, "main", quotation.id, "non_profit_price", css="price-value number alert"),
                _edit_cell(quotation.final_selling_price, "main", quotation.id, "final_selling_price", css="price-value featured number"),
            ),
            _row(
                _edit_cell(quotation.irradiation_core_count, "main", quotation.id, "irradiation_core_count", css="fee-value number"),
                _edit_cell(quotation.irradiation_core_fee, "main", quotation.id, "irradiation_core_fee", css="fee-value number"),
                _edit_cell(_percent(quotation.operating_expense_rate), "main", quotation.id, "operating_expense_rate", css="fee-value number", scale="percent"),
                _edit_cell(_percent(quotation.monthly_interest), "main", quotation.id, "monthly_interest", css="fee-value number", scale="percent"),
                _edit_cell(_percent(quotation.corporate_tax_rate), "main", quotation.id, "corporate_tax_rate", css="fee-value number", scale="percent"),
                _cell("", colspan=2, css="price-value"),
            ),
        ]
    )
    return _wrap_preview_html(quotation.quotation_code or "", table_html)


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
        return f"{value:f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _percent(value, suffix: bool = True) -> str:
    result = _format_value(_decimal(value) * Decimal("100"))
    return f"{result}%" if suffix else result


def _wrap_preview_html(quotation_code: str, table_html: str) -> str:
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
        .cell-wrap {{ position: relative; display: flex; align-items: center; }}
        .locked-cell {{ background: #dbeafe !important; }}
        .lock-mark {{ position: absolute; right: 2px; top: 1px; color: #1d4ed8; font-size: 10px; font-family: Arial, sans-serif; }}
    </style>
</head>
<body>
    <div class="meta">数据库实时渲染 · 成本分析号：{html.escape(quotation_code)}</div>
    <div class="sheet"><table><colgroup><col><col><col><col><col><col><col><col><col></colgroup><tbody>{table_html}</tbody></table></div>
</body>
</html>"""
