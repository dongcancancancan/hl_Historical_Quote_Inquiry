from datetime import datetime
from decimal import Decimal, InvalidOperation

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

