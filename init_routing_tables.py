from sqlalchemy import text

import app.models.routing  # noqa: F401
from app.database import Base, engine


def _schema_for(conn, table_name: str) -> str:
    return conn.execute(
        text("SELECT SCHEMA_NAME(schema_id) FROM sys.tables WHERE name=:name"),
        {"name": table_name},
    ).scalar() or "dbo"


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
IF OBJECT_ID(N'[{schema}].[quotation_routing_policy]', N'U') IS NULL
BEGIN
    CREATE TABLE [{schema}].[quotation_routing_policy] (
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [tenant_id] NVARCHAR(50) NOT NULL,
        [policy_name] NVARCHAR(200) NOT NULL,
        [status] NVARCHAR(20) NOT NULL CONSTRAINT [DF_qrp_status] DEFAULT ('draft'),
        [enabled] BIT NOT NULL CONSTRAINT [DF_qrp_enabled] DEFAULT ((0)),
        [prompt_rules] NVARCHAR(MAX) NOT NULL,
        [confidence_threshold] NUMERIC(18,4) NULL,
        [version_no] INT NOT NULL CONSTRAINT [DF_qrp_version_no] DEFAULT ((1)),
        [llm_model] NVARCHAR(100) NULL,
        [route_scope] NVARCHAR(100) NULL,
        [remark] NVARCHAR(500) NULL,
        [creator] NVARCHAR(64) NULL,
        [create_time] DATETIME2 NOT NULL CONSTRAINT [DF_qrp_create_time] DEFAULT (SYSDATETIME()),
        [updater] NVARCHAR(64) NULL,
        [update_time] DATETIME2 NOT NULL CONSTRAINT [DF_qrp_update_time] DEFAULT (SYSDATETIME()),
        [deleted] BIT NOT NULL CONSTRAINT [DF_qrp_deleted] DEFAULT ((0))
    );
END
"""
            )
        )
        for index, columns in {
            "ix_qrp_tenant_id": "[tenant_id]",
            "ix_qrp_status": "[status]",
            "ix_qrp_enabled": "[enabled]",
            "ix_qrp_route_scope": "[route_scope]",
            "ix_qrp_deleted": "[deleted]",
        }.items():
            _create_index_if_missing(conn, schema, "quotation_routing_policy", index, columns)

        conn.execute(
            text(
                f"""
IF OBJECT_ID(N'[{schema}].[quotation_routing_decision_run]', N'U') IS NULL
BEGIN
    CREATE TABLE [{schema}].[quotation_routing_decision_run] (
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [quotation_main_id] INT NOT NULL,
        [bpm_instance_id] INT NULL,
        [calculation_run_id] INT NULL,
        [quotation_code] NVARCHAR(100) NOT NULL,
        [bpm_no] NVARCHAR(100) NULL,
        [tenant_id] NVARCHAR(50) NULL,
        [policy_id] INT NULL,
        [route_scene] NVARCHAR(50) NOT NULL,
        [trigger_source] NVARCHAR(50) NULL,
        [input_snapshot] NVARCHAR(MAX) NOT NULL,
        [candidate_skills] NVARCHAR(MAX) NULL,
        [llm_model] NVARCHAR(100) NULL,
        [llm_prompt_text] NVARCHAR(MAX) NULL,
        [llm_response_text] NVARCHAR(MAX) NULL,
        [decision_json] NVARCHAR(MAX) NULL,
        [confidence] NUMERIC(18,4) NULL,
        [final_action] NVARCHAR(50) NOT NULL,
        [final_skill] NVARCHAR(100) NULL,
        [adopt_status] NVARCHAR(20) NOT NULL CONSTRAINT [DF_qrdr_adopt_status] DEFAULT ('pending'),
        [error_message] NVARCHAR(1000) NULL,
        [operator] NVARCHAR(64) NOT NULL,
        [create_time] DATETIME2 NOT NULL CONSTRAINT [DF_qrdr_create_time] DEFAULT (SYSDATETIME()),
        CONSTRAINT [FK_qrdr_quotation_main] FOREIGN KEY ([quotation_main_id])
            REFERENCES [{schema}].[quotation_main]([id]),
        CONSTRAINT [FK_qrdr_bpm_instance] FOREIGN KEY ([bpm_instance_id])
            REFERENCES [{schema}].[quotation_bpm_instance]([id]),
        CONSTRAINT [FK_qrdr_calculation_run] FOREIGN KEY ([calculation_run_id])
            REFERENCES [{schema}].[quotation_calculation_run]([id]),
        CONSTRAINT [FK_qrdr_policy] FOREIGN KEY ([policy_id])
            REFERENCES [{schema}].[quotation_routing_policy]([id])
    );
END
"""
            )
        )
        for index, columns in {
            "ix_qrdr_quotation_main_id": "[quotation_main_id]",
            "ix_qrdr_bpm_instance_id": "[bpm_instance_id]",
            "ix_qrdr_calculation_run_id": "[calculation_run_id]",
            "ix_qrdr_quotation_code": "[quotation_code]",
            "ix_qrdr_bpm_no": "[bpm_no]",
            "ix_qrdr_tenant_id": "[tenant_id]",
            "ix_qrdr_policy_id": "[policy_id]",
            "ix_qrdr_route_scene": "[route_scene]",
            "ix_qrdr_final_skill": "[final_skill]",
            "ix_qrdr_adopt_status": "[adopt_status]",
            "ix_qrdr_create_time": "[create_time]",
            "ix_qrdr_quotation_main_create_time": "[quotation_main_id], [create_time]",
            "ix_qrdr_bpm_instance_create_time": "[bpm_instance_id], [create_time]",
            "ix_qrdr_adopt_status_create_time": "[adopt_status], [create_time]",
        }.items():
            _create_index_if_missing(conn, schema, "quotation_routing_decision_run", index, columns)


def main() -> None:
    if engine.dialect.name == "mssql":
        _mssql_migrate()
    else:
        Base.metadata.create_all(bind=engine)
    print("routing tables ready")


if __name__ == "__main__":
    main()
