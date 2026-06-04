from sqlalchemy import text

from app.database import engine


def main():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT
                fk.name AS fk_name,
                OBJECT_SCHEMA_NAME(fkc.parent_object_id) AS child_schema,
                OBJECT_NAME(fkc.parent_object_id) AS child_table,
                pc.name AS child_column,
                OBJECT_SCHEMA_NAME(fkc.referenced_object_id) AS parent_schema,
                OBJECT_NAME(fkc.referenced_object_id) AS parent_table,
                rc.name AS parent_column,
                fk.delete_referential_action_desc AS delete_action
            FROM sys.foreign_key_columns fkc
            JOIN sys.foreign_keys fk ON fkc.constraint_object_id = fk.object_id
            JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
            JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
            WHERE OBJECT_NAME(fkc.referenced_object_id) = 'quotation_main'
        """)).mappings().all()

        for row in rows:
            if row["delete_action"] == "CASCADE":
                continue
            child = f"[{row['child_schema']}].[{row['child_table']}]"
            parent = f"[{row['parent_schema']}].[{row['parent_table']}]"
            fk_name = f"[{row['fk_name']}]"
            child_col = f"[{row['child_column']}]"
            parent_col = f"[{row['parent_column']}]"
            conn.execute(text(f"ALTER TABLE {child} DROP CONSTRAINT {fk_name}"))
            conn.execute(text(
                f"ALTER TABLE {child} WITH CHECK ADD CONSTRAINT {fk_name} "
                f"FOREIGN KEY({child_col}) REFERENCES {parent} ({parent_col}) ON DELETE CASCADE"
            ))
            conn.execute(text(f"ALTER TABLE {child} CHECK CONSTRAINT {fk_name}"))
            print(f"enabled cascade: {row['fk_name']} on {row['child_schema']}.{row['child_table']}")


if __name__ == "__main__":
    main()
