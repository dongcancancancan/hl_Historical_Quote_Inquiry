from sqlalchemy import Column, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class PVCMaterialPrice(Base):
    __tablename__ = "PVC_MaterialPrice"
    __table_args__ = {"schema": "dbo"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    KW = Column(String(50))
    PRD_NO = Column(String(50), nullable=False)
    NAME = Column(String(200))
    UT = Column(String(50))
    UP = Column(Numeric(28, 6))
    CREATEDATE = Column(DateTime, server_default=func.now())
    USR = Column(String(50))
    MODIFYDATE = Column(DateTime)
    REM = Column(String(200))
    HSYF = Column(DateTime)


class PVCMaterialPriceLog(Base):
    __tablename__ = "PVC_MaterialPrice_Log"
    __table_args__ = {"schema": "dbo"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    material_price_id = Column(Integer, index=True)
    PRD_NO = Column(String(50), nullable=False, index=True)
    action = Column(String(20), nullable=False)
    before_data = Column(Text)
    after_data = Column(Text)
    operator = Column(String(64), nullable=False)
    operate_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)

