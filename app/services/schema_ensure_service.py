import logging

from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)


BPM_INSTANCE_NUMERIC_COLUMNS = {
    "transport_fee": "NUMERIC(18, 4) NULL",
    "other_fee": "NUMERIC(18, 4) NULL",
    "net_profit_rate": "NUMERIC(18, 4) NULL",
    "customs_fee": "NUMERIC(18, 4) NULL",
    "order_meterage": "NUMERIC(18, 4) NULL",
    "operating_expense_rate": "NUMERIC(18, 4) NULL",
    "monthly_interest": "NUMERIC(18, 4) NULL",
    "corporate_tax_rate": "NUMERIC(18, 4) NULL",
}


def ensure_runtime_schema() -> None:
    """Apply small additive schema updates required by the app at startup."""
    db = SessionLocal()
    try:
        schema_row = db.execute(text("""
            SELECT TOP 1 TABLE_SCHEMA
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = 'quotation_bpm_instance'
            ORDER BY CASE WHEN TABLE_SCHEMA = 'dbo' THEN 0 ELSE 1 END, TABLE_SCHEMA
        """)).mappings().first()
        if not schema_row:
            return
        schema = str(schema_row["TABLE_SCHEMA"])
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
            db.execute(text(f"ALTER TABLE [{escaped_schema}].[quotation_bpm_instance] ADD [{escaped_column}] {ddl}"))
            logger.info("Added quotation_bpm_instance.%s", column)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Runtime schema ensure failed")
        raise
    finally:
        db.close()
