from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session


def list_pvc_boms(db: Session, keyword: str = "") -> list[dict]:
    params = {"keyword": f"%{(keyword or '').strip()}%"}
    filter_sql = ""
    if keyword and keyword.strip():
        filter_sql = """
            AND (
                m.BOM_NO LIKE :keyword
                OR m.MJNAME LIKE :keyword
                OR EXISTS (
                    SELECT 1 FROM dbo.PVC_BOM_Detail d
                    WHERE d.BOM_NO = m.BOM_NO AND d.MJNAME LIKE :keyword
                )
            )
        """
    rows = db.execute(text(f"""
        SELECT
            m.id,
            m.BOM_NO,
            COALESCE(NULLIF(m.MJNAME, ''), MIN(d.MJNAME)) AS MJNAME,
            m.totalweight,
            m.totoalAMT,
            m.COST,
            m.process,
            m.package,
            m.saleprice,
            m.USR,
            m.MODIFYDATE
        FROM dbo.PVC_BOM_Main m
        LEFT JOIN dbo.PVC_BOM_Detail d ON d.BOM_NO = m.BOM_NO
        WHERE m.BOM_NO LIKE 'C%'
        {filter_sql}
        GROUP BY m.id, m.BOM_NO, m.MJNAME, m.totalweight, m.totoalAMT, m.COST, m.process, m.package, m.saleprice, m.USR, m.MODIFYDATE
        ORDER BY m.BOM_NO
    """), params).mappings().all()
    return [_serialize_main(row) for row in rows]


def get_pvc_bom_detail(db: Session, bom_no: str) -> dict | None:
    main = db.execute(text("""
        SELECT
            m.id,
            m.BOM_NO,
            COALESCE(NULLIF(m.MJNAME, ''), MIN(d.MJNAME)) AS MJNAME,
            m.totalweight,
            m.totoalAMT,
            m.COST,
            m.process,
            m.package,
            m.saleprice,
            m.USR,
            m.MODIFYDATE
        FROM dbo.PVC_BOM_Main m
        LEFT JOIN dbo.PVC_BOM_Detail d ON d.BOM_NO = m.BOM_NO
        WHERE m.BOM_NO = :bom_no AND m.BOM_NO LIKE 'C%'
        GROUP BY m.id, m.BOM_NO, m.MJNAME, m.totalweight, m.totoalAMT, m.COST, m.process, m.package, m.saleprice, m.USR, m.MODIFYDATE
    """), {"bom_no": bom_no}).mappings().first()
    if not main:
        return None
    details = db.execute(text("""
        SELECT id, BOM_NO, MJNAME, PRD_NO, ZJNAME, UT, QTY, UP, AMT
        FROM dbo.PVC_BOM_Detail
        WHERE BOM_NO = :bom_no
        ORDER BY id
    """), {"bom_no": bom_no}).mappings().all()
    return {
        "main": _serialize_main(main),
        "details": [_serialize_detail(row) for row in details],
    }


def update_pvc_bom_fees(db: Session, bom_no: str, process_fee, package_fee, operator: str) -> dict:
    process_value = _to_decimal(process_fee, "加工费")
    package_value = _to_decimal(package_fee, "包装费")
    main = db.execute(text("""
        SELECT BOM_NO, COST
        FROM dbo.PVC_BOM_Main
        WHERE BOM_NO = :bom_no AND BOM_NO LIKE 'C%'
    """), {"bom_no": bom_no}).mappings().first()
    if not main:
        raise ValueError("未找到 PVC 母料 BOM")
    cost = main["COST"]
    if cost is None:
        cost = _calculate_cost(db, bom_no)
    saleprice = Decimal(cost or 0) + process_value + package_value
    db.execute(text("""
        UPDATE dbo.PVC_BOM_Main
        SET process = :process,
            package = :package,
            saleprice = :saleprice,
            USR = :operator,
            MODIFYDATE = :modifydate
        WHERE BOM_NO = :bom_no
    """), {
        "process": process_value,
        "package": package_value,
        "saleprice": saleprice,
        "operator": operator,
        "modifydate": datetime.now(),
        "bom_no": bom_no,
    })
    db.commit()
    result = get_pvc_bom_detail(db, bom_no)
    if not result:
        raise ValueError("PVC 母料 BOM 更新后读取失败")
    return result["main"]


def calculate_pvc_bom(db: Session, bom_no: str, operator: str) -> dict:
    main = db.execute(text("""
        SELECT BOM_NO, process, package
        FROM dbo.PVC_BOM_Main
        WHERE BOM_NO = :bom_no AND BOM_NO LIKE 'C%'
    """), {"bom_no": bom_no}).mappings().first()
    if not main:
        raise ValueError("未找到 PVC 母料 BOM")
    details = db.execute(text("""
        SELECT id, PRD_NO, UT, QTY
        FROM dbo.PVC_BOM_Detail
        WHERE BOM_NO = :bom_no
        ORDER BY id
    """), {"bom_no": bom_no}).mappings().all()
    if not details:
        raise ValueError("该 PVC 母料没有 BOM 明细")

    missing_prices = []
    total_weight = Decimal("0")
    total_amount = Decimal("0")
    updates = []
    for detail in details:
        material_no = detail["PRD_NO"] or ""
        bom_unit = _normalize_unit(detail["UT"])
        qty = Decimal(detail["QTY"] or 0)
        price = _get_latest_material_price(db, material_no, bom_unit)
        if not price:
            missing_prices.append(material_no)
            continue
        price_unit = _normalize_unit(price["UT"])
        unit_price = _round2(price["UP"])
        amount = _round2(_amount_by_unit(qty, bom_unit, Decimal(price["UP"] or 0), price_unit))
        total_weight += _weight_as_kg(qty, bom_unit)
        total_amount += amount
        updates.append({
            "id": detail["id"],
            "unit_price": unit_price,
            "amount": amount,
        })

    if missing_prices:
        joined = "、".join(sorted({item for item in missing_prices if item}))
        raise ValueError(f"以下材料没有维护 PVC 材料价格：{joined}")

    total_weight = _round2(total_weight)
    total_amount = _round2(total_amount)
    cost = Decimal("0") if total_weight == 0 else _round2(total_amount / total_weight)
    process_fee = _round2(main["process"] if main["process"] is not None else Decimal("0.50"))
    package_fee = _round2(main["package"] if main["package"] is not None else Decimal("0.04"))
    saleprice = _round2(cost + process_fee + package_fee)
    now = datetime.now()

    for item in updates:
        db.execute(text("""
            UPDATE dbo.PVC_BOM_Detail
            SET UP = :unit_price,
                AMT = :amount,
                USR = :operator,
                MODIFYDATE = :modifydate
            WHERE id = :id
        """), {
            "unit_price": item["unit_price"],
            "amount": item["amount"],
            "operator": operator,
            "modifydate": now,
            "id": item["id"],
        })

    db.execute(text("""
        UPDATE dbo.PVC_BOM_Main
        SET totalweight = :total_weight,
            totoalAMT = :total_amount,
            COST = :cost,
            process = :process_fee,
            package = :package_fee,
            saleprice = :saleprice,
            USR = :operator,
            MODIFYDATE = :modifydate
        WHERE BOM_NO = :bom_no
    """), {
        "total_weight": total_weight,
        "total_amount": total_amount,
        "cost": cost,
        "process_fee": process_fee,
        "package_fee": package_fee,
        "saleprice": saleprice,
        "operator": operator,
        "modifydate": now,
        "bom_no": bom_no,
    })
    db.commit()
    result = get_pvc_bom_detail(db, bom_no)
    if not result:
        raise ValueError("PVC 母料 BOM 计算后读取失败")
    return result


def _calculate_cost(db: Session, bom_no: str) -> Decimal:
    row = db.execute(text("""
        SELECT
            SUM(CASE WHEN UPPER(LTRIM(RTRIM(ISNULL(UT, '')))) = 'G' THEN QTY / 1000.0 ELSE QTY END) AS totalweight,
            SUM(AMT) AS totalamt
        FROM dbo.PVC_BOM_Detail
        WHERE BOM_NO = :bom_no
    """), {"bom_no": bom_no}).mappings().first()
    totalweight = Decimal(row["totalweight"] or 0)
    totalamt = Decimal(row["totalamt"] or 0)
    return Decimal("0") if totalweight == 0 else totalamt / totalweight


def _get_latest_material_price(db: Session, material_no: str, bom_unit: str):
    rows = db.execute(text("""
        SELECT TOP 20 PRD_NO, UT, UP, HSYF, CREATEDATE, id
        FROM dbo.PVC_MaterialPrice
        WHERE PRD_NO = :material_no AND UP IS NOT NULL
        ORDER BY
            CASE WHEN UPPER(LTRIM(RTRIM(ISNULL(UT, '')))) = :bom_unit THEN 0 ELSE 1 END,
            CASE WHEN HSYF IS NULL THEN 1 ELSE 0 END,
            HSYF DESC,
            CREATEDATE DESC,
            id DESC
    """), {"material_no": material_no, "bom_unit": bom_unit}).mappings().all()
    return rows[0] if rows else None


def _amount_by_unit(qty: Decimal, bom_unit: str, unit_price: Decimal, price_unit: str) -> Decimal:
    if bom_unit == price_unit:
        return qty * unit_price
    if bom_unit == "G" and price_unit == "KG":
        return qty / Decimal("1000") * unit_price
    if bom_unit == "KG" and price_unit == "G":
        return qty * Decimal("1000") * unit_price
    return qty * unit_price


def _weight_as_kg(qty: Decimal, unit: str) -> Decimal:
    return qty / Decimal("1000") if unit == "G" else qty


def _normalize_unit(value) -> str:
    return str(value or "").strip().upper()


def _round2(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _serialize_main(row) -> dict:
    return {
        "id": row["id"],
        "bom_no": row["BOM_NO"] or "",
        "name": row["MJNAME"] or "",
        "total_weight": _decimal_text(row["totalweight"]),
        "total_amount": _decimal_text(row["totoalAMT"]),
        "cost": _decimal_text(row["COST"]),
        "process_fee": _decimal_text(row["process"]),
        "package_fee": _decimal_text(row["package"]),
        "sale_price": _decimal_text(row["saleprice"]),
        "operator": row["USR"] or "",
        "modify_time": row["MODIFYDATE"].isoformat() if row["MODIFYDATE"] else None,
    }


def _serialize_detail(row) -> dict:
    return {
        "id": row["id"],
        "bom_no": row["BOM_NO"] or "",
        "parent_name": row["MJNAME"] or "",
        "material_no": row["PRD_NO"] or "",
        "material_name": row["ZJNAME"] or "",
        "unit": row["UT"] or "",
        "quantity": _decimal_text(row["QTY"]),
        "unit_price": _decimal_text(row["UP"]),
        "amount": _decimal_text(row["AMT"]),
    }


def _to_decimal(value, label: str) -> Decimal:
    if value in (None, ""):
        raise ValueError(f"{label}不能为空")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if result < 0:
        raise ValueError(f"{label}不能小于 0")
    return result


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):f}".rstrip("0").rstrip(".") or "0"
