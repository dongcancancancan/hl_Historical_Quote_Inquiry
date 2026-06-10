from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.calc_param import QuotationCalcParam
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.models.user import User
from app.services.calc_param_service import (
    DEFAULT_COPPER_ROD_PROCESS_FEE,
    DEFAULT_VAT_RATE,
    get_or_create_calc_params,
    serialize_calc_params,
    update_calc_params,
)

REVIEW_PENDING = "pending"
REVIEW_QUOTED = "quoted"


def normalize_bpm_no(value: str | None) -> str:
    return (value or "").strip().upper()


def get_bpm_instance(db: Session, instance_id: int | None) -> QuotationBpmInstance | None:
    if not instance_id:
        return None
    return (
        db.query(QuotationBpmInstance)
        .filter(
            QuotationBpmInstance.id == instance_id,
            QuotationBpmInstance.deleted == False,
        )
        .first()
    )


def get_accessible_quotation_context(
    db: Session,
    quotation_code: str | None,
    instance_id: int | None,
    tenant_id: str,
    creator_name: str,
    is_admin: bool = False,
    is_reviewer: bool = False,
) -> tuple[QuotationMain, QuotationBpmInstance | None] | None:
    if instance_id:
        instance = get_bpm_instance(db, instance_id)
        if not instance:
            return None
        quotation = (
            db.query(QuotationMain)
            .filter(
                QuotationMain.id == instance.quotation_main_id,
                QuotationMain.deleted == False,
            )
            .first()
        )
        if not quotation:
            return None
        if not _can_access(db, quotation, instance, tenant_id, creator_name, is_admin, is_reviewer):
            return None
        return quotation, instance

    if not quotation_code:
        return None
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
    if not quotation:
        return None
    instance = (
        db.query(QuotationBpmInstance)
        .filter(
            QuotationBpmInstance.quotation_main_id == quotation.id,
            QuotationBpmInstance.deleted == False,
        )
        .order_by(QuotationBpmInstance.create_time.desc(), QuotationBpmInstance.id.desc())
        .first()
    )
    if instance and not is_reviewer and instance.review_status == REVIEW_QUOTED:
        return None
    return quotation, instance


def get_existing_quotation(db: Session, tenant_id: str, quotation_code: str) -> QuotationMain | None:
    return (
        db.query(QuotationMain)
        .filter(
            QuotationMain.tenant_id == tenant_id,
            QuotationMain.quotation_code == quotation_code,
            QuotationMain.deleted == False,
        )
        .first()
    )


def ensure_bpm_instance(
    db: Session,
    quotation: QuotationMain,
    bpm_no: str,
    quote_date: date | None,
    upload_user: str,
    source_file_path: str,
) -> QuotationBpmInstance:
    bpm_no = normalize_bpm_no(bpm_no)
    if not bpm_no:
        raise ValueError("请先填写 BPM流程号")

    instance = (
        db.query(QuotationBpmInstance)
        .filter(
            QuotationBpmInstance.quotation_main_id == quotation.id,
            QuotationBpmInstance.bpm_no == bpm_no,
            QuotationBpmInstance.deleted == False,
        )
        .first()
    )
    now = datetime.now()
    if instance:
        if instance.review_status == REVIEW_QUOTED:
            raise ValueError("该 BPM流程号已报价，不能覆盖原报价实例；如需再次询价，请使用新的 BPM流程号上传")
        instance.quote_date = quote_date or instance.quote_date or quotation.analysis_date
        instance.source_file_path = source_file_path or instance.source_file_path
        instance.upload_user = upload_user or instance.upload_user
        instance.upload_time = now
        instance.updater = upload_user or instance.updater
        instance.update_time = now
        return instance

    params = (
        db.query(QuotationCalcParam)
        .filter(QuotationCalcParam.quotation_main_id == quotation.id)
        .first()
    )
    instance = QuotationBpmInstance(
        tenant_id=quotation.tenant_id,
        quotation_main_id=quotation.id,
        quotation_code=quotation.quotation_code or "",
        bpm_no=bpm_no,
        quote_date=quote_date or quotation.analysis_date,
        source_file_path=source_file_path,
        upload_user=upload_user,
        review_status=REVIEW_PENDING,
        copper_price=params.copper_price if params else None,
        copper_rod_process_fee=params.copper_rod_process_fee if params else DEFAULT_COPPER_ROD_PROCESS_FEE,
        vat_rate=params.vat_rate if params else DEFAULT_VAT_RATE,
        transport_fee=quotation.transport_fee,
        other_fee=quotation.other_fee,
        net_profit_rate=quotation.net_profit_rate,
        customs_fee=quotation.customs_fee,
        order_meterage=quotation.order_meterage,
        operating_expense_rate=quotation.operating_expense_rate,
        monthly_interest=quotation.monthly_interest,
        corporate_tax_rate=quotation.corporate_tax_rate,
        cost=quotation.cost,
        profit_selling_price=quotation.profit_selling_price,
        non_profit_price=quotation.non_profit_price,
        final_selling_price=quotation.final_selling_price,
        creator=upload_user,
        updater=upload_user,
        update_time=now,
        deleted=False,
    )
    db.add(instance)
    db.flush()
    return instance


def get_review_status_for(quotation: QuotationMain, instance: QuotationBpmInstance | None = None) -> str:
    if instance:
        return REVIEW_QUOTED if instance.review_status == REVIEW_QUOTED else REVIEW_PENDING
    try:
        import json

        tags = json.loads(quotation.extracted_tags or "{}")
        return REVIEW_QUOTED if isinstance(tags, dict) and tags.get("review_status") == REVIEW_QUOTED else REVIEW_PENDING
    except Exception:
        return REVIEW_PENDING


def set_instance_review_status(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    status: str,
    updater: str,
) -> str:
    if status not in {REVIEW_PENDING, REVIEW_QUOTED}:
        raise ValueError("无效的报价状态")
    now = datetime.now()
    if instance:
        instance.review_status = status
        instance.quoted_time = now if status == REVIEW_QUOTED else None
        snapshot_instance_from_quotation(instance, quotation, updater, now)
        db.commit()
        return status

    import json

    tags = {}
    try:
        tags = json.loads(quotation.extracted_tags or "{}")
        tags = tags if isinstance(tags, dict) else {}
    except Exception:
        tags = {}
    tags["review_status"] = status
    quotation.extracted_tags = json.dumps(tags, ensure_ascii=False)
    quotation.updater = updater
    quotation.update_time = now
    db.commit()
    return status


def serialize_instance_calc_params(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
) -> dict:
    params = get_or_create_calc_params(db, quotation, operator)
    data = serialize_calc_params(params)
    if instance:
        data.update(
            {
                "instance_id": instance.id,
                "bpm_no": instance.bpm_no or "",
                "quote_date": instance.quote_date.isoformat() if instance.quote_date else None,
                "copper_price": _decimal_text(instance.copper_price),
                "copper_rod_process_fee": _decimal_text(instance.copper_rod_process_fee or DEFAULT_COPPER_ROD_PROCESS_FEE),
                "vat_rate": _decimal_text(instance.vat_rate or DEFAULT_VAT_RATE),
                "transport_fee": _decimal_text(instance.transport_fee if instance.transport_fee is not None else quotation.transport_fee),
                "other_fee": _decimal_text(instance.other_fee if instance.other_fee is not None else quotation.other_fee),
                "net_profit_rate": _decimal_text(instance.net_profit_rate if instance.net_profit_rate is not None else quotation.net_profit_rate),
                "customs_fee": _decimal_text(instance.customs_fee if instance.customs_fee is not None else quotation.customs_fee),
                "order_meterage": _decimal_text(instance.order_meterage if instance.order_meterage is not None else quotation.order_meterage),
                "operating_expense_rate": _decimal_text(instance.operating_expense_rate if instance.operating_expense_rate is not None else quotation.operating_expense_rate),
                "monthly_interest": _decimal_text(instance.monthly_interest if instance.monthly_interest is not None else quotation.monthly_interest),
                "corporate_tax_rate": _decimal_text(instance.corporate_tax_rate if instance.corporate_tax_rate is not None else quotation.corporate_tax_rate),
                "updater": instance.updater or data.get("updater") or "",
                "update_time": instance.update_time.isoformat() if instance.update_time else data.get("update_time"),
            }
        )
    return data


def update_instance_calc_params(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    data: dict,
    operator: str,
) -> dict:
    params = update_calc_params(db, quotation, data, operator)
    if instance:
        instance.copper_price = params.copper_price
        instance.copper_rod_process_fee = params.copper_rod_process_fee
        instance.vat_rate = params.vat_rate
        _apply_review_params_to_instance(instance, quotation, data)
        instance.updater = operator
        instance.update_time = datetime.now()
        db.commit()
        return serialize_instance_calc_params(db, quotation, instance, operator)
    return serialize_calc_params(params)


def sync_instance_calc_params_to_engine(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    operator: str,
) -> None:
    if not instance:
        get_or_create_calc_params(db, quotation, operator)
        return
    data = {
        "copper_price": _decimal_text(instance.copper_price),
        "copper_rod_process_fee": _decimal_text(instance.copper_rod_process_fee or DEFAULT_COPPER_ROD_PROCESS_FEE),
        "vat_rate": _decimal_text(instance.vat_rate or DEFAULT_VAT_RATE),
    }
    update_calc_params(db, quotation, data, operator)
    _apply_instance_review_params_to_quotation(quotation, instance)


def snapshot_instance_from_quotation(
    instance: QuotationBpmInstance | None,
    quotation: QuotationMain,
    operator: str,
    now: datetime | None = None,
) -> None:
    if not instance:
        return
    now = now or datetime.now()
    instance.quotation_code = quotation.quotation_code or instance.quotation_code
    instance.cost = quotation.cost
    instance.profit_selling_price = quotation.profit_selling_price
    instance.non_profit_price = quotation.non_profit_price
    instance.final_selling_price = quotation.final_selling_price
    _apply_quotation_review_params_to_instance(instance, quotation)
    instance.updater = operator
    instance.update_time = now



def _can_access(
    db: Session,
    quotation: QuotationMain,
    instance: QuotationBpmInstance | None,
    tenant_id: str,
    creator_name: str,
    is_admin: bool,
    is_reviewer: bool,
) -> bool:
    if is_admin or is_reviewer:
        return True
    if quotation.tenant_id not in {tenant_id, None}:
        return False
    admin_names = _admin_creator_names(db)
    if admin_names and quotation.creator in admin_names:
        return False
    if instance and instance.review_status == REVIEW_QUOTED:
        return False
    return True


def _admin_creator_names(db: Session) -> list[str]:
    rows = db.query(User.username, User.display_name).filter(User.is_admin == True).all()
    names = set()
    for username, display_name in rows:
        if username:
            names.add(username)
        if display_name:
            names.add(display_name)
    return list(names)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"


REVIEW_PARAM_FIELDS = [
    "transport_fee",
    "other_fee",
    "net_profit_rate",
    "customs_fee",
    "order_meterage",
    "operating_expense_rate",
    "monthly_interest",
    "corporate_tax_rate",
]


def _optional_decimal(value):
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("审价参数格式不正确") from exc
    if result < 0:
        raise ValueError("审价参数不能小于 0")
    return result


def _apply_review_params_to_instance(instance: QuotationBpmInstance, quotation: QuotationMain, data: dict) -> None:
    for field in REVIEW_PARAM_FIELDS:
        if field in data:
            setattr(instance, field, _optional_decimal(data.get(field)))
        elif getattr(instance, field, None) is None:
            setattr(instance, field, getattr(quotation, field, None))


def _apply_instance_review_params_to_quotation(quotation: QuotationMain, instance: QuotationBpmInstance) -> None:
    for field in REVIEW_PARAM_FIELDS:
        value = getattr(instance, field, None)
        if value is not None:
            setattr(quotation, field, value)
    quotation.vat_rate = instance.vat_rate or quotation.vat_rate


def _apply_quotation_review_params_to_instance(instance: QuotationBpmInstance, quotation: QuotationMain) -> None:
    for field in REVIEW_PARAM_FIELDS:
        setattr(instance, field, getattr(quotation, field, None))
