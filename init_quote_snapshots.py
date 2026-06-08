from sqlalchemy import text

from app.database import Base, engine


def _schema_for(conn, table_name: str) -> str:
    return conn.execute(
        text("SELECT SCHEMA_NAME(schema_id) FROM sys.tables WHERE name=:name"),
        {"name": table_name},
    ).scalar() or "dbo"


def _add_column_if_missing(conn, schema: str, table: str, column: str, ddl: str) -> None:
    conn.execute(
        text(
            f"""
IF COL_LENGTH('{schema}.{table}', '{column}') IS NULL
BEGIN
    ALTER TABLE [{schema}].[{table}] ADD {ddl};
END
"""
        )
    )


def _create_index_if_missing(conn, schema: str, table: str, index: str, columns: str) -> None:
    conn.execute(
        text(
            f"""
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = '{index}'
      AND object_id = OBJECT_ID('[{schema}].[{table}]')
)
BEGIN
    CREATE INDEX [{index}] ON [{schema}].[{table}] ({columns});
END
"""
        )
    )


def _mssql_migrate() -> None:
    with engine.begin() as conn:
        schema = _schema_for(conn, "quotation_main")
        conn.execute(
            text(
                f"""
IF OBJECT_ID(N'[{schema}].[quotation_calculation_run]', N'U') IS NULL
BEGIN
    CREATE TABLE [{schema}].[quotation_calculation_run] (
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [quotation_main_id] INT NOT NULL,
        [bpm_instance_id] INT NULL,
        [quotation_code] NVARCHAR(100) NOT NULL,
        [bpm_no] NVARCHAR(100) NULL,
        [run_type] NVARCHAR(50) NOT NULL,
        [status] NVARCHAR(20) NOT NULL CONSTRAINT [DF_qcr_status] DEFAULT ('success'),
        [params_snapshot] NVARCHAR(MAX) NULL,
        [result_summary] NVARCHAR(MAX) NULL,
        [skill_version] NVARCHAR(50) NOT NULL CONSTRAINT [DF_qcr_skill_version] DEFAULT ('v1'),
        [is_adopted] BIT NOT NULL CONSTRAINT [DF_qcr_is_adopted] DEFAULT ((0)),
        [operator] NVARCHAR(64) NOT NULL,
        [start_time] DATETIME2 NOT NULL CONSTRAINT [DF_qcr_start_time] DEFAULT (SYSDATETIME()),
        [finish_time] DATETIME2 NOT NULL CONSTRAINT [DF_qcr_finish_time] DEFAULT (SYSDATETIME()),
        [create_time] DATETIME2 NOT NULL CONSTRAINT [DF_qcr_create_time] DEFAULT (SYSDATETIME()),
        CONSTRAINT [FK_qcr_quotation_main] FOREIGN KEY ([quotation_main_id])
            REFERENCES [{schema}].[quotation_main]([id]),
        CONSTRAINT [FK_qcr_bpm_instance] FOREIGN KEY ([bpm_instance_id])
            REFERENCES [{schema}].[quotation_bpm_instance]([id])
    );
END
"""
            )
        )
        for index, columns in {
            "ix_qcr_quotation_main_id": "[quotation_main_id]",
            "ix_qcr_bpm_instance_id": "[bpm_instance_id]",
            "ix_qcr_quotation_code": "[quotation_code]",
            "ix_qcr_bpm_no": "[bpm_no]",
            "ix_qcr_run_type": "[run_type]",
            "ix_qcr_status": "[status]",
            "ix_qcr_is_adopted": "[is_adopted]",
            "ix_qcr_start_time": "[start_time]",
            "ix_qcr_create_time": "[create_time]",
        }.items():
            _create_index_if_missing(conn, schema, "quotation_calculation_run", index, columns)

        trace_columns = {
            "run_id": "[run_id] INT NULL",
            "bpm_instance_id": "[bpm_instance_id] INT NULL",
            "entity_type": "[entity_type] NVARCHAR(20) NULL",
            "entity_id": "[entity_id] INT NULL",
            "display_label": "[display_label] NVARCHAR(100) NULL",
            "skill_id": "[skill_id] NVARCHAR(100) NULL",
            "cell_key": "[cell_key] NVARCHAR(200) NULL",
            "depends_on": "[depends_on] NVARCHAR(MAX) NULL",
            "source_refs": "[source_refs] NVARCHAR(MAX) NULL",
        }
        for column, ddl in trace_columns.items():
            _add_column_if_missing(conn, schema, "quotation_calculation_trace", column, ddl)
        for index, columns in {
            "ix_qct_run_id": "[run_id]",
            "ix_qct_bpm_instance_id": "[bpm_instance_id]",
            "ix_qct_entity_type": "[entity_type]",
            "ix_qct_entity_id": "[entity_id]",
            "ix_qct_skill_id": "[skill_id]",
            "ix_qct_cell_key": "[cell_key]",
        }.items():
            _create_index_if_missing(conn, schema, "quotation_calculation_trace", index, columns)

        conn.execute(
            text(
                f"""
IF OBJECT_ID(N'[{schema}].[quotation_quote_snapshot]', N'U') IS NULL
BEGIN
    CREATE TABLE [{schema}].[quotation_quote_snapshot] (
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [quotation_main_id] INT NOT NULL,
        [bpm_instance_id] INT NOT NULL,
        [calculation_run_id] INT NULL,
        [quotation_code] NVARCHAR(100) NOT NULL,
        [bpm_no] NVARCHAR(100) NOT NULL,
        [quote_date] DATE NULL,
        [snapshot_data] NVARCHAR(MAX) NOT NULL,
        [final_selling_price] NUMERIC(18,4) NULL,
        [quoted_by] NVARCHAR(64) NOT NULL,
        [quoted_time] DATETIME2 NOT NULL CONSTRAINT [DF_qqs_quoted_time] DEFAULT (SYSDATETIME()),
        [active] BIT NOT NULL CONSTRAINT [DF_qqs_active] DEFAULT ((1)),
        [deleted] BIT NOT NULL CONSTRAINT [DF_qqs_deleted] DEFAULT ((0)),
        [create_time] DATETIME2 NOT NULL CONSTRAINT [DF_qqs_create_time] DEFAULT (SYSDATETIME()),
        CONSTRAINT [FK_qqs_quotation_main] FOREIGN KEY ([quotation_main_id])
            REFERENCES [{schema}].[quotation_main]([id]),
        CONSTRAINT [FK_qqs_bpm_instance] FOREIGN KEY ([bpm_instance_id])
            REFERENCES [{schema}].[quotation_bpm_instance]([id]),
        CONSTRAINT [FK_qqs_calculation_run] FOREIGN KEY ([calculation_run_id])
            REFERENCES [{schema}].[quotation_calculation_run]([id])
    );
END
"""
            )
        )
        for index, columns in {
            "ix_qqs_quotation_main_id": "[quotation_main_id]",
            "ix_qqs_bpm_instance_id": "[bpm_instance_id]",
            "ix_qqs_calculation_run_id": "[calculation_run_id]",
            "ix_qqs_quotation_code": "[quotation_code]",
            "ix_qqs_bpm_no": "[bpm_no]",
            "ix_qqs_quote_date": "[quote_date]",
            "ix_qqs_quoted_time": "[quoted_time]",
            "ix_qqs_active": "[active]",
            "ix_qqs_deleted": "[deleted]",
            "ix_qqs_create_time": "[create_time]",
        }.items():
            _create_index_if_missing(conn, schema, "quotation_quote_snapshot", index, columns)


def main() -> None:
    if engine.dialect.name == "mssql":
        _mssql_migrate()
    else:
        Base.metadata.create_all(bind=engine)
    print("quote snapshot tables ready")


if __name__ == "__main__":
    main()
