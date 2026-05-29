import io
import sys
from sqlalchemy import create_engine, create_mock_engine
from app.database import Base
from app.core.config import settings
# 导入模型以确保 Base.metadata 收集到所有的表结构
from app.models.quotation import QuotationMain, QuotationMaterial, QuotationProcess, QuotationCostSummary

sql_file_path = "init_db.sql"
buf = io.StringIO()

def dump(sql, *multiparams, **params):
    """
    SQLAlchemy mock engine 回调函数，用于拦截 SQL 并写入缓冲区
    """
    compiled_sql = sql.compile(dialect=engine.dialect)
    buf.write(str(compiled_sql).strip() + ";\n\n")

# 1. 生成 SQL 语句并持久化到文件
print("Generating SQL DDL...")
engine = create_mock_engine(settings.DATABASE_URL, executor=dump)
Base.metadata.create_all(engine, checkfirst=False)

with open(sql_file_path, "w", encoding="utf-8") as f:
    f.write(buf.getvalue())
print(f"SQL file '{sql_file_path}' generated successfully.")

# 2. 执行建表语句到 PostgreSQL 数据库
print(f"Connecting to real database and executing DDL...")
real_engine = create_engine(settings.DATABASE_URL)
try:
    Base.metadata.create_all(real_engine)
    print("Tables created successfully in the PostgreSQL database!")
except Exception as e:
    print(f"\n[ERROR] Failed to connect and create tables in PostgreSQL: {e}", file=sys.stderr)
    print("Please check your database connection or run the 'init_db.sql' manually.")
    sys.exit(1)
