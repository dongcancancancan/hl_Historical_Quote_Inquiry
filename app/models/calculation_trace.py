from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class QuotationCalculationRun(Base):
    __tablename__ = "quotation_calculation_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), nullable=False, index=True)
    bpm_instance_id = Column(Integer, ForeignKey("quotation_bpm_instance.id"), index=True)
    quotation_code = Column(String(100), nullable=False, index=True)
    bpm_no = Column(String(100), index=True)
    run_type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="success", index=True)
    params_snapshot = Column(Text)
    result_summary = Column(Text)
    skill_version = Column(String(50), nullable=False, default="v1")
    is_adopted = Column(Boolean, nullable=False, default=False, index=True)
    operator = Column(String(64), nullable=False)
    start_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    finish_time = Column(DateTime, nullable=False, server_default=func.now())
    create_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)


class QuotationCalculationTrace(Base):
    __tablename__ = "quotation_calculation_trace"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("quotation_calculation_run.id"), index=True)
    bpm_instance_id = Column(Integer, ForeignKey("quotation_bpm_instance.id"), index=True)
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), nullable=False, index=True)
    quotation_code = Column(String(100), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("quotation_material.id"), index=True)
    entity_type = Column(String(20), index=True)
    entity_id = Column(Integer, index=True)
    calc_type = Column(String(50), nullable=False, index=True)
    field_name = Column(String(50), nullable=False)
    display_label = Column(String(100))
    skill_id = Column(String(100), index=True)
    cell_key = Column(String(200), index=True)
    formula = Column(String(500), nullable=False)
    input_data = Column(Text)
    depends_on = Column(Text)
    source_refs = Column(Text)
    process_text = Column(Text)
    result_value = Column(Numeric(18, 4))
    operator = Column(String(64), nullable=False)
    create_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)


class QuotationQuoteSnapshot(Base):
    __tablename__ = "quotation_quote_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), nullable=False, index=True)
    bpm_instance_id = Column(Integer, ForeignKey("quotation_bpm_instance.id"), nullable=False, index=True)
    calculation_run_id = Column(Integer, ForeignKey("quotation_calculation_run.id"), index=True)
    quotation_code = Column(String(100), nullable=False, index=True)
    bpm_no = Column(String(100), nullable=False, index=True)
    quote_date = Column(Date, index=True)
    snapshot_data = Column(Text, nullable=False)
    final_selling_price = Column(Numeric(18, 4))
    quoted_by = Column(String(64), nullable=False)
    quoted_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    deleted = Column(Boolean, nullable=False, default=False, index=True)
    create_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)
