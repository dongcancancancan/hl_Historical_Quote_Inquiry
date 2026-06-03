from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class QuotationCalculationTrace(Base):
    __tablename__ = "quotation_calculation_trace"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), nullable=False, index=True)
    quotation_code = Column(String(100), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("quotation_material.id"), index=True)
    calc_type = Column(String(50), nullable=False, index=True)
    field_name = Column(String(50), nullable=False)
    formula = Column(String(500), nullable=False)
    input_data = Column(Text)
    process_text = Column(Text)
    result_value = Column(Numeric(18, 4))
    operator = Column(String(64), nullable=False)
    create_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)

