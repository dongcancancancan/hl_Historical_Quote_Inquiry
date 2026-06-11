import logging

from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)


BPM_INSTANCE_NUMERIC_COLUMNS = {
    "ul_label_fee": "NUMERIC(18, 4) NULL",
    "transport_fee": "NUMERIC(18, 4) NULL",
    "other_fee": "NUMERIC(18, 4) NULL",
    "net_profit_rate": "NUMERIC(18, 4) NULL",
    "customs_fee": "NUMERIC(18, 4) NULL",
    "order_meterage": "NUMERIC(18, 4) NULL",
    "operating_expense_rate": "NUMERIC(18, 4) NULL",
    "monthly_interest": "NUMERIC(18, 10) NULL",
    "corporate_tax_rate": "NUMERIC(18, 4) NULL",
    "material_ratio": "NUMERIC(18, 4) NULL",
    "order_weight": "NUMERIC(18, 4) NULL",
}

# 列名 → 目标精度：启动时自动将 NUMERIC 列 scale 不够的 ALTER 到目标值
COLUMN_PRECISION_UPGRADES: dict[str, dict[str, str]] = {
    "quotation_main": {
        "monthly_interest": "NUMERIC(18, 10)",
    },
    "quotation_bpm_instance": {
        "monthly_interest": "NUMERIC(18, 10)",
    },
}


def _table_schema(db, table_name: str) -> str | None:
    row = db.execute(text("""
        SELECT TOP 1 TABLE_SCHEMA
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_NAME = :table
        ORDER BY CASE WHEN TABLE_SCHEMA = 'dbo' THEN 0 ELSE 1 END, TABLE_SCHEMA
    """), {"table": table_name}).mappings().first()
    return str(row["TABLE_SCHEMA"]) if row else None


def _ensure_column_precision(db, schema: str, table: str, column: str, desired_ddl: str) -> None:
    """若列存在但精度不够，则 ALTER 修正。"""
    try:
        row = db.execute(text("""
            SELECT DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :schema
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
        """), {"schema": schema, "table": table, "column": column}).mappings().first()
        if not row:
            return
        if row["DATA_TYPE"] != "numeric":
            return
        # 从 desired_ddl 中解析目标 scale，如 "NUMERIC(18, 10)" → 10
        import re
        m = re.search(r"NUMERIC\s*\(\s*\d+\s*,\s*(\d+)\s*\)", desired_ddl, re.IGNORECASE)
        target_scale = int(m.group(1)) if m else 8
        if row["NUMERIC_SCALE"] >= target_scale:
            return
        escaped_table = table.replace("]", "]]")
        escaped_column = column.replace("]", "]]")
        db.execute(text(
            f"ALTER TABLE [{schema}].[{escaped_table}] ALTER COLUMN [{escaped_column}] {desired_ddl}"
        ))
        logger.info(
            "Altered %s.%s.%s NUMERIC(%d,%d) → %s",
            schema, table, column, row["NUMERIC_PRECISION"], row["NUMERIC_SCALE"], desired_ddl,
        )
    except Exception:
        logger.exception("Column precision ensure failed for %s.%s.%s", schema, table, column)


def ensure_runtime_schema() -> None:
    """Apply small additive schema updates required by the app at startup."""
    db = SessionLocal()
    try:
        # 1) 为 quotation_bpm_instance 添加缺失列
        schema = _table_schema(db, "quotation_bpm_instance")
        if schema:
            escaped_schema = schema.replace("]", "]]")
            for column, ddl in BPM_INSTANCE_NUMERIC_COLUMNS.items():
                exists = db.execute(text("""
                    SELECT 1
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = :schema
                      AND TABLE_NAME = 'quotation_bpm_instance'
                      AND COLUMN_NAME = :column
                """), {"schema": schema, "column": column}).first()
                if exists:
                    continue
                escaped_column = column.replace("]", "]]")
                db.execute(text(
                    f"ALTER TABLE [{escaped_schema}].[quotation_bpm_instance] ADD [{escaped_column}] {ddl}"
                ))
                logger.info("Added quotation_bpm_instance.%s", column)
            db.commit()

        # 2) 修正已有列的精度
        for table_name, columns in COLUMN_PRECISION_UPGRADES.items():
            table_schema = _table_schema(db, table_name)
            if not table_schema:
                continue
            for column, ddl in columns.items():
                _ensure_column_precision(db, table_schema, table_name, column, ddl)
        db.commit()

    except Exception:
        db.rollback()
        logger.exception("Runtime schema ensure failed")
        raise
    finally:
        db.close()
