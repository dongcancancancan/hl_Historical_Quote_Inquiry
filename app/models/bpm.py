"""BPM 系统相关模型（dbo schema，只读）"""
from sqlalchemy import Column, String
from app.database import Base


class BPM_B015_List(Base):
    """BPM 流程号与成本分析号映射视图（只读）"""
    __tablename__ = "BPM_B015_List"
    __table_args__ = {"schema": "dbo"}

    流水号 = Column(String(100), primary_key=True, comment="BPM 流程流水号")
    成本分析号 = Column(String(100), comment="成本分析号")
