import psycopg2
from app.core.config import settings

def fix_foreign_keys():
    conn_str = settings.DATABASE_URL.replace("postgresql://", "postgresql://")
    print(f"Connecting to {conn_str}...")
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    
    queries = [
        """
        ALTER TABLE quotation_material 
        DROP CONSTRAINT IF EXISTS quotation_material_quotation_id_fkey,
        ADD CONSTRAINT quotation_material_quotation_id_fkey 
        FOREIGN KEY (quotation_id) REFERENCES quotation_main(id) ON DELETE CASCADE;
        """,
        """
        ALTER TABLE quotation_process 
        DROP CONSTRAINT IF EXISTS quotation_process_quotation_id_fkey,
        ADD CONSTRAINT quotation_process_quotation_id_fkey 
        FOREIGN KEY (quotation_id) REFERENCES quotation_main(id) ON DELETE CASCADE;
        """,
        """
        ALTER TABLE quotation_cost_summary 
        DROP CONSTRAINT IF EXISTS quotation_cost_summary_quotation_id_fkey,
        ADD CONSTRAINT quotation_cost_summary_quotation_id_fkey 
        FOREIGN KEY (quotation_id) REFERENCES quotation_main(id) ON DELETE CASCADE;
        """
    ]
    
    for q in queries:
        try:
            cur.execute(q)
            print("Successfully executed FK update.")
        except Exception as e:
            print(f"Error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    print("Database foreign keys fixed for cascading deletes!")

if __name__ == "__main__":
    fix_foreign_keys()
