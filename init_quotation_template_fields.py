from sqlalchemy import text

from app.database import engine


def main():
    with engine.begin() as conn:
        schema_name = conn.execute(text("""
            SELECT TOP 1 TABLE_SCHEMA
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = 'quotation_main'
            ORDER BY CASE WHEN TABLE_SCHEMA = 'dbo' THEN 0 ELSE 1 END
        """)).scalar()
        if not schema_name:
            raise RuntimeError("quotation_main table not found")

        full_name = f"[{schema_name}].[quotation_main]"
        object_name = f"{schema_name}.quotation_main"

        columns = {
            "bpm_no": "NVARCHAR(100) NULL",
            "package_method": "NVARCHAR(200) NULL",
            "remark": "NVARCHAR(500) NULL",
        }
        for column, ddl in columns.items():
            exists = conn.execute(
                text("SELECT COL_LENGTH(:object_name, :column_name)"),
                {"object_name": object_name, "column_name": column},
            ).scalar()
            if exists is None:
                conn.execute(text(f"ALTER TABLE {full_name} ADD {column} {ddl}"))

        index_exists = conn.execute(text("""
            SELECT 1
            FROM sys.indexes
            WHERE name = 'IX_quotation_main_bpm_no'
              AND object_id = OBJECT_ID(:object_name)
        """), {"object_name": object_name}).first()
        if not index_exists:
            conn.execute(text(f"CREATE INDEX IX_quotation_main_bpm_no ON {full_name}(bpm_no)"))

    print(f"quotation_main template fields are ready on {schema_name}.quotation_main")


if __name__ == "__main__":
    main()
