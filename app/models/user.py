from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False, comment="租户编码")
    display_name = Column(String(100), comment="租户显示名称")
    created_at = Column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, comment="所属租户ID")
    username = Column(String(64), nullable=False, comment="用户名")
    password_hash = Column(String(256), nullable=False, comment="密码哈希")
    display_name = Column(String(100), comment="显示名称")
    created_at = Column(DateTime, server_default=func.now())


class Sys_BPMUser(Base):
    """BPM 系统用户表（只读，映射已存在的 dbo.Sys_BPMUsers）"""
    __tablename__ = "Sys_BPMUsers"
    __table_args__ = {"schema": "dbo"}

    Account = Column(String(10), primary_key=True, comment="BPM 账号")
    Password = Column(String(100), comment="MD5 密码")
    DisplayName = Column(String(50), comment="显示名称")
