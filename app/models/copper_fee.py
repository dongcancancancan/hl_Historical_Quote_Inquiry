from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class CopperProcessingFee(Base):
    __tablename__ = "copper_processing_fee"
    __table_args__ = (
        UniqueConstraint("copper_type", "diameter", "tin_price_basis", name="uq_copper_fee_match"),
        {"schema": "dbo"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    copper_type = Column(String(10), nullable=False, index=True, comment="BC 裸铜 / TC 镀锡铜")
    diameter = Column(Numeric(10, 4), nullable=False, index=True, comment="线径")
    tin_price_basis = Column(Numeric(18, 4), nullable=False, default=0, comment="TC 锡价段；BC 固定为 0")
    processing_fee = Column(Numeric(18, 4), nullable=False, comment="加工费")
    minimum_fee = Column(Numeric(18, 4), comment="最低加工费")
    remark = Column(String(200), comment="备注")
    enabled = Column(Boolean, nullable=False, default=True, index=True, comment="是否启用")
    creator = Column(String(64), nullable=False, comment="创建人")
    create_time = Column(DateTime, nullable=False, server_default=func.now(), comment="创建时间")
    updater = Column(String(64), comment="更新人")
    update_time = Column(DateTime, nullable=False, server_default=func.now(), comment="更新时间")


class CopperProcessingFeeLog(Base):
    __tablename__ = "copper_processing_fee_log"
    __table_args__ = {"schema": "dbo"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    fee_id = Column(Integer, index=True, comment="加工费主表 ID")
    action = Column(String(20), nullable=False, comment="CREATE / UPDATE / DELETE / IMPORT")
    before_data = Column(Text, comment="修改前 JSON")
    after_data = Column(Text, comment="修改后 JSON")
    operator = Column(String(64), nullable=False, comment="操作人")
    operate_time = Column(DateTime, nullable=False, server_default=func.now(), comment="操作时间")
