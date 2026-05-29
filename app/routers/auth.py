from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import Tenant, User
from app.core.auth import hash_password, verify_password, create_token

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
    """用户注册"""
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

    token = create_token(user.id, user.username, tenant.code, tenant.display_name)
    return {
        "token": token,
        "tenant_id": tenant.code,
        "tenant_name": tenant.display_name,
        "username": user.username,
    }


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """用户登录"""
    # 按用户名查找所有匹配的用户（可能跨租户），选第一个密码匹配的
    candidates = db.query(User).filter(User.username == req.username).all()
    if not candidates:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    for user in candidates:
        if verify_password(req.password, user.password_hash):
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            token = create_token(user.id, user.username, tenant.code, tenant.display_name)
            return {
                "token": token,
                "tenant_id": tenant.code,
                "tenant_name": tenant.display_name,
                "username": user.username,
            }

    raise HTTPException(status_code=401, detail="用户名或密码错误")
