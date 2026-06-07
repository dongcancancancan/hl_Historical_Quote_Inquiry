import json
from datetime import datetime

from sqlalchemy import text

from app.database import SessionLocal, engine
from app.models.calc_param import QuotationCalcParam
from app.models.quotation import QuotationBpmInstance, QuotationMain
from app.services.bpm_instance_service import REVIEW_QUOTED, normalize_bpm_no
from app.services.calc_param_service import DEFAULT_COPPER_ROD_PROCESS_FEE, DEFAULT_VAT_RATE


def _schema_for(conn, table_name: str) -> str:
    row = conn.execute(
        text("SELECT SCHEMA_NAME(schema_id) FROM sys.tables WHERE name=:name"),
        {"name": table_name},
    ).scalar()
    return row or "dbo"


def _sqlserver_migrate() -> None:
    with engine.begin() as conn:
        schema = _schema_for(conn, "quotation_main")
        conn.execute(
            text(
                f"""
IF COL_LENGTH('{schema}.quotation_main', 'content_hash') IS NULL
BEGIN
    ALTER TABLE [{schema}].[quotation_main] ADD [content_hash] NVARCHAR(64) NULL;
END
"""
            )
        )
        conn.execute(
            text(
                f"""
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_quotation_main_content_hash'
      AND object_id = OBJECT_ID('[{schema}].[quotation_main]')
)
BEGIN
    CREATE INDEX [ix_quotation_main_content_hash] ON [{schema}].[quotation_main] ([content_hash]);
END
"""
            )
        )
        conn.execute(
            text(
                f"""
IF OBJECT_ID(N'[{schema}].[quotation_bpm_instance]', N'U') IS NULL
BEGIN
    CREATE TABLE [{schema}].[quotation_bpm_instance] (
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [tenant_id] NVARCHAR(50) NULL,
        [quotation_main_id] INT NOT NULL,
        [quotation_code] NVARCHAR(100) NOT NULL,
        [bpm_no] NVARCHAR(100) NOT NULL,
        [quote_date] DATE NULL,
        [source_file_path] NVARCHAR(500) NULL,
        [upload_user] NVARCHAR(64) NULL,
        [upload_time] DATETIME2 NOT NULL CONSTRAINT [DF_qbi_upload_time] DEFAULT (SYSDATETIME()),
        [review_status] NVARCHAR(20) NOT NULL CONSTRAINT [DF_qbi_review_status] DEFAULT ('pending'),
        [copper_price] NUMERIC(18,4) NULL,
        [copper_rod_process_fee] NUMERIC(18,4) NULL,
        [vat_rate] NUMERIC(18,4) NULL,
        [cost] NUMERIC(18,4) NULL,
        [profit_selling_price] NUMERIC(18,4) NULL,
        [non_profit_price] NUMERIC(18,4) NULL,
        [final_selling_price] NUMERIC(18,4) NULL,
        [quoted_time] DATETIME2 NULL,
        [creator] NVARCHAR(64) NULL,
        [create_time] DATETIME2 NOT NULL CONSTRAINT [DF_qbi_create_time] DEFAULT (SYSDATETIME()),
        [updater] NVARCHAR(64) NULL,
        [update_time] DATETIME2 NOT NULL CONSTRAINT [DF_qbi_update_time] DEFAULT (SYSDATETIME()),
        [deleted] BIT NOT NULL CONSTRAINT [DF_qbi_deleted] DEFAULT ((0)),
        CONSTRAINT [FK_qbi_quotation_main] FOREIGN KEY ([quotation_main_id])
            REFERENCES [{schema}].[quotation_main]([id])
    );
END
"""
            )
        )
        for index_name, columns in {
            "ix_qbi_tenant_id": "[tenant_id]",
            "ix_qbi_quotation_main_id": "[quotation_main_id]",
            "ix_qbi_quotation_code": "[quotation_code]",
            "ix_qbi_bpm_no": "[bpm_no]",
            "ix_qbi_quote_date": "[quote_date]",
            "ix_qbi_upload_user": "[upload_user]",
            "ix_qbi_review_status": "[review_status]",
        }.items():
            conn.execute(
                text(
                    f"""
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = '{index_name}'
      AND object_id = OBJECT_ID('[{schema}].[quotation_bpm_instance]')
)
BEGIN
    CREATE INDEX [{index_name}] ON [{schema}].[quotation_bpm_instance] ({columns});
END
"""
                )
            )
        conn.execute(
            text(
                f"""
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ux_qbi_main_bpm_active'
      AND object_id = OBJECT_ID('[{schema}].[quotation_bpm_instance]')
)
BEGIN
    CREATE UNIQUE INDEX [ux_qbi_main_bpm_active]
    ON [{schema}].[quotation_bpm_instance] ([quotation_main_id], [bpm_no])
    WHERE [deleted] = 0;
END
"""
            )
        )


def _review_status_from_tags(raw_tags: str | None) -> str:
    try:
        tags = json.loads(raw_tags or "{}")
        if isinstance(tags, dict) and tags.get("review_status") == REVIEW_QUOTED:
            return REVIEW_QUOTED
    except Exception:
        pass
    return "pending"


def _backfill_instances() -> int:
    db = SessionLocal()
    created = 0
    try:
        quotations = db.query(QuotationMain).filter(QuotationMain.deleted == False).all()
        for quotation in quotations:
            bpm_no = normalize_bpm_no(quotation.bpm_no)
            if not bpm_no:
                continue
            exists = (
                db.query(QuotationBpmInstance)
                .filter(
                    QuotationBpmInstance.quotation_main_id == quotation.id,
                    QuotationBpmInstance.bpm_no == bpm_no,
                    QuotationBpmInstance.deleted == False,
                )
                .first()
            )
            if exists:
                continue
            params = (
                db.query(QuotationCalcParam)
                .filter(QuotationCalcParam.quotation_main_id == quotation.id)
                .first()
            )
            now = datetime.now()
            instance = QuotationBpmInstance(
                tenant_id=quotation.tenant_id,
                quotation_main_id=quotation.id,
                quotation_code=quotation.quotation_code or "",
                bpm_no=bpm_no,
                quote_date=quotation.analysis_date,
                source_file_path=quotation.original_file_path,
                upload_user=quotation.creator,
                upload_time=quotation.create_time or now,
                review_status=_review_status_from_tags(quotation.extracted_tags),
                copper_price=params.copper_price if params else None,
                copper_rod_process_fee=params.copper_rod_process_fee if params else DEFAULT_COPPER_ROD_PROCESS_FEE,
                vat_rate=params.vat_rate if params else DEFAULT_VAT_RATE,
                cost=quotation.cost,
                profit_selling_price=quotation.profit_selling_price,
                non_profit_price=quotation.non_profit_price,
                final_selling_price=quotation.final_selling_price,
                creator=quotation.creator,
                create_time=quotation.create_time or now,
                updater=quotation.updater or quotation.creator,
                update_time=quotation.update_time or now,
                deleted=False,
            )
            db.add(instance)
            created += 1
        db.commit()
        return created
    finally:
        db.close()


def main() -> None:
    if engine.dialect.name != "mssql":
        from app.database import Base

        Base.metadata.create_all(bind=engine)
    else:
        _sqlserver_migrate()
    created = _backfill_instances()
    print(f"quotation_bpm_instance ready, backfilled {created} rows")


if __name__ == "__main__":
    main()
