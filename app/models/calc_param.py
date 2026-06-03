from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.sql import func

from app.database import Base


class QuotationCalcParam(Base):
    __tablename__ = "quotation_calc_params"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quotation_main_id = Column(Integer, ForeignKey("quotation_main.id"), nullable=False, unique=True, index=True)
    quotation_code = Column(String(100), nullable=False, index=True)
    copper_price = Column(Numeric(18, 4), comment="铜价，元/吨")
    copper_rod_process_fee = Column(Numeric(18, 4), nullable=False, default=1055, comment="铜块加工为铜杆加工费，元/吨")
    vat_rate = Column(Numeric(18, 4), nullable=False, default=1.13, comment="增值税税率")
    creator = Column(String(64))
    create_time = Column(DateTime, nullable=False, server_default=func.now())
    updater = Column(String(64))
    update_time = Column(DateTime, nullable=False, server_default=func.now())

