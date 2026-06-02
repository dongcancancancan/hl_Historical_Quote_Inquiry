import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.models.pvc_material_price import PVCMaterialPrice, PVCMaterialPriceLog


def list_material_prices(db: Session, keyword: str = ""):
    query = db.query(PVCMaterialPrice)
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(
            PVCMaterialPrice.PRD_NO.like(like),
            PVCMaterialPrice.NAME.like(like),
        ))
    rows = query.order_by(
        PVCMaterialPrice.PRD_NO,
        PVCMaterialPrice.HSYF.desc(),
        PVCMaterialPrice.CREATEDATE.desc(),
        PVCMaterialPrice.id.desc(),
    ).all()
    seen_codes = set()
    items = []
    for row in rows:
        item = serialize_material_price(row)
        item["is_current"] = row.PRD_NO not in seen_codes
        seen_codes.add(row.PRD_NO)
        items.append(item)
    return items


def create_material_price(db: Session, data: dict, operator: str, action: str = "CREATE", commit: bool = True):
    normalized = _normalize_payload(data)
    _ensure_not_duplicate(db, normalized)
    row = PVCMaterialPrice(**normalized, USR=operator, MODIFYDATE=datetime.now())
    db.add(row)
    db.flush()
    _write_log(db, row.id, row.PRD_NO, action, None, _snapshot(row), operator)
    if commit:
        db.commit()
    return row


def update_material_price(db: Session, row: PVCMaterialPrice, data: dict, operator: str):
    before = _snapshot(row)
    normalized = _normalize_payload(data, row)
    _ensure_not_duplicate(db, normalized, exclude_id=row.id)
    for key, value in normalized.items():
        setattr(row, key, value)
    row.USR = operator
    row.MODIFYDATE = datetime.now()
    _write_log(db, row.id, row.PRD_NO, "UPDATE", before, _snapshot(row), operator)
    db.commit()
    return row


def delete_material_price(db: Session, row: PVCMaterialPrice, operator: str):
    before = _snapshot(row)
    _write_log(db, row.id, row.PRD_NO, "DELETE", before, None, operator)
    db.delete(row)
    db.commit()


def list_material_price_logs(db: Session, prd_no: str = ""):
    query = db.query(PVCMaterialPriceLog)
    if prd_no:
        query = query.filter(PVCMaterialPriceLog.PRD_NO == prd_no.strip())
    return [
        serialize_log(row)
        for row in query.order_by(PVCMaterialPriceLog.operate_time.desc(), PVCMaterialPriceLog.id.desc()).limit(500).all()
    ]


def initialize_from_bom(db: Session, operator: str = "SYSTEM") -> dict:
    existing = {
        (row.PRD_NO, row.UT or "", _decimal_text(row.UP))
        for row in db.query(PVCMaterialPrice).all()
    }
    source_rows = db.execute(text("""
        SELECT PRD_NO, MIN(ZJNAME) AS NAME, UT, UP
        FROM dbo.PVC_BOM_Detail
        WHERE PRD_NO IS NOT NULL AND LTRIM(RTRIM(PRD_NO)) <> '' AND UP IS NOT NULL
        GROUP BY PRD_NO, UT, UP
        ORDER BY PRD_NO, UT, UP
    """)).mappings().all()
    missing_rows = db.execute(text("""
        SELECT DISTINCT PRD_NO, ZJNAME AS NAME, UT
        FROM dbo.PVC_BOM_Detail
        WHERE PRD_NO IS NOT NULL AND UP IS NULL
        ORDER BY PRD_NO
    """)).mappings().all()
    created = 0
    skipped = 0
    for source in source_rows:
        key = (source["PRD_NO"], source["UT"] or "", _decimal_text(source["UP"]))
        if key in existing:
            skipped += 1
            continue
        create_material_price(db, {
            "prd_no": source["PRD_NO"],
            "name": source["NAME"],
            "unit": source["UT"],
            "unit_price": source["UP"],
            "remark": "由历史 PVC BOM 明细初始化",
        }, operator, action="IMPORT", commit=False)
        existing.add(key)
        created += 1
    db.commit()
    return {
        "created": created,
        "skipped": skipped,
        "missing_price_items": [dict(row) for row in missing_rows],
    }


def serialize_material_price(row: PVCMaterialPrice) -> dict:
    return {
        "id": row.id,
        "prd_no": row.PRD_NO,
        "name": row.NAME or "",
        "unit": row.UT or "",
        "unit_price": _decimal_text(row.UP),
        "effective_date": row.HSYF.date().isoformat() if row.HSYF else None,
        "remark": row.REM or "",
        "operator": row.USR or "",
        "create_time": row.CREATEDATE.isoformat() if row.CREATEDATE else None,
        "update_time": row.MODIFYDATE.isoformat() if row.MODIFYDATE else None,
    }


def serialize_log(row: PVCMaterialPriceLog) -> dict:
    return {
        "id": row.id,
        "material_price_id": row.material_price_id,
        "prd_no": row.PRD_NO,
        "action": row.action,
        "before_data": json.loads(row.before_data) if row.before_data else None,
        "after_data": json.loads(row.after_data) if row.after_data else None,
        "operator": row.operator,
        "operate_time": row.operate_time.isoformat() if row.operate_time else None,
    }


def _normalize_payload(data: dict, existing: PVCMaterialPrice | None = None) -> dict:
    prd_no = str(data.get("prd_no", existing.PRD_NO if existing else "") or "").strip()
    name = str(data.get("name", existing.NAME if existing else "") or "").strip()
    unit = str(data.get("unit", existing.UT if existing else "") or "").strip().upper()
    price_raw = data.get("unit_price", existing.UP if existing else None)
    if not prd_no or not name or not unit or price_raw in (None, ""):
        raise ValueError("材料编号、材料名称、材料单位和未税单价均为必填项")
    try:
        unit_price = Decimal(str(price_raw))
    except InvalidOperation as exc:
        raise ValueError("未税单价格式不正确") from exc
    if unit_price < 0:
        raise ValueError("未税单价不能小于 0")
    effective_raw = data.get("effective_date", existing.HSYF if existing else None)
    effective_date = _parse_date(effective_raw)
    return {
        "PRD_NO": prd_no,
        "NAME": name,
        "UT": unit,
        "UP": unit_price,
        "HSYF": effective_date,
        "REM": str(data.get("remark", existing.REM if existing else "") or "").strip(),
    }


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("生效日期格式不正确") from exc


def _ensure_not_duplicate(db: Session, data: dict, exclude_id: int | None = None):
    query = db.query(PVCMaterialPrice).filter(
        PVCMaterialPrice.PRD_NO == data["PRD_NO"],
        PVCMaterialPrice.UT == data["UT"],
        PVCMaterialPrice.UP == data["UP"],
    )
    if data["HSYF"] is None:
        query = query.filter(PVCMaterialPrice.HSYF.is_(None))
    else:
        query = query.filter(PVCMaterialPrice.HSYF == data["HSYF"])
    if exclude_id:
        query = query.filter(PVCMaterialPrice.id != exclude_id)
    if query.first():
        raise ValueError("相同料号、单位、单价和生效日期的价格版本已存在")


def _write_log(db: Session, row_id: int, prd_no: str, action: str, before: dict | None, after: dict | None, operator: str):
    db.add(PVCMaterialPriceLog(
        material_price_id=row_id,
        PRD_NO=prd_no,
        action=action,
        before_data=json.dumps(before, ensure_ascii=False) if before else None,
        after_data=json.dumps(after, ensure_ascii=False) if after else None,
        operator=operator,
    ))


def _snapshot(row: PVCMaterialPrice) -> dict:
    return serialize_material_price(row)


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"

