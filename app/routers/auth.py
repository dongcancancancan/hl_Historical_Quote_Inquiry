import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import Tenant, User, Sys_BPMUser
from app.core.auth import hash_password, hash_password_md5, verify_password, create_token

logger = logging.getLogger(__name__)
router = APIRouter()


class RegisterRequest(BaseModel):
    tenant_code: str   # "engineering" or "finance"
    username: str
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/tenants")
def list_tenants(db: Session = Depends(get_db)):
    """获取可用租户列表（注册页下拉框用）"""
    tenants = db.query(Tenant).all()
    return [{"code": t.code, "display_name": t.display_name} for t in tenants]


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册（保留接口，前端不展示注册入口）"""
    tenant = db.query(Tenant).filter(Tenant.code == req.tenant_code).first()
    if not tenant:
        raise HTTPException(status_code=400, detail="无效的租户编码")

    existing = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.username == req.username,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该用户名在此租户下已存在")

    user = User(
        tenant_id=tenant.id,
        username=req.username,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.username,
    )
    db.add(user)
    db.commit()

    display_name = user.display_name or user.username
    token = create_token(user.id, user.username, tenant.code, tenant.display_name, display_name)
    return {
        "token": token,
        "tenant_id": tenant.code,
        "tenant_name": tenant.display_name,
        "username": user.username,
        "display_name": display_name,
    }


def _authenticate_local(db: Session, username: str, password: str):
    """尝试从本地 users 表认证"""
    candidates = db.query(User).filter(User.username == username).all()
    for user in candidates:
        if verify_password(password, user.password_hash):
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            display_name = user.display_name or user.username
            token = create_token(user.id, user.username, tenant.code, tenant.display_name, display_name)
            return {
                "token": token,
                "tenant_id": tenant.code,
                "tenant_name": tenant.display_name,
                "username": user.username,
                "display_name": display_name,
            }
    return None


def _authenticate_bpm(db: Session, username: str, password: str):
    """尝试从 BPM 用户表认证，成功后自动在 users 表创建/更新记录"""
    bpm_user = db.query(Sys_BPMUser).filter(Sys_BPMUser.Account == username).first()
    if not bpm_user:
        return None

    # 验证 MD5 密码
    import hashlib
    if hashlib.md5(password.encode()).hexdigest() != bpm_user.Password.strip():
        return None

    # BPM 认证成功 → 在 users 表中创建或更新本地用户
    # 默认归属到 engineering 租户
    tenant = db.query(Tenant).filter(Tenant.code == "engineering").first()
    if not tenant:
        logger.error("Tenant 'engineering' not found, cannot migrate BPM user")
        return None

    local_user = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.username == username,
    ).first()

    md5_hash = f"md5:{bpm_user.Password.strip()}"
    display_name = bpm_user.DisplayName.strip() if bpm_user.DisplayName else username

    if local_user:
        # 更新密码为 BPM 密码（如果用户之前通过注册创建，密码不一致）
        if local_user.password_hash != md5_hash:
            local_user.password_hash = md5_hash
            local_user.display_name = display_name
            db.commit()
    else:
        local_user = User(
            tenant_id=tenant.id,
            username=username,
            password_hash=md5_hash,
            display_name=display_name,
        )
        db.add(local_user)
        db.commit()

    token = create_token(local_user.id, username, tenant.code, tenant.display_name, display_name)
    logger.info(f"BPM user '{username}' authenticated and synced to local users")
    return {
        "token": token,
        "tenant_id": tenant.code,
        "tenant_name": tenant.display_name,
        "username": username,
        "display_name": display_name,
    }


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """用户登录：先查本地 users 表，再查 BPM 系统表"""
    # 1. 先尝试本地用户认证
    result = _authenticate_local(db, req.username, req.password)
    if result:
        return result

    # 2. 再尝试 BPM 系统认证
    result = _authenticate_bpm(db, req.username, req.password)
    if result:
        return result

    raise HTTPException(status_code=401, detail="用户名或密码错误")
