import psycopg2
from app.core.config import settings

def update_schema():
    conn_str = settings.DATABASE_URL.replace("postgresql://", "postgresql://")
    print(f"Connecting to {conn_str}...")
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    
    print("Checking and adding missing columns to quotation_main...")
    try:
        cur.execute("ALTER TABLE quotation_main ADD COLUMN original_file_path VARCHAR;")
        print("Added original_file_path")
    except Exception as e:
        print(f"original_file_path: {e}")
        conn.rollback()
        
    try:
        cur.execute("ALTER TABLE quotation_main ADD COLUMN extracted_tags JSON;")
        print("Added extracted_tags")
    except Exception as e:
        print(f"extracted_tags: {e}")
        conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    update_schema()
