"""认证模块：密码哈希 + JWT 签发/校验 + FastAPI 依赖注入"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from app.core.config import settings


@dataclass
class UserContext:
    user_id: int
    username: str
    tenant_id: str
    tenant_name: str
    display_name: str
    is_admin: bool = False
    role: str = "engineering"

    @property
    def is_reviewer(self) -> bool:
        return self.role == "reviewer"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"


def hash_password_md5(password: str) -> str:
    """兼容 BPM 系统的 MD5 密码格式，存储时加 md5: 前缀以区分"""
    return f"md5:{hashlib.md5(password.encode()).hexdigest()}"


def verify_password(password: str, stored: str) -> bool:
    # 兼容 BPM 的 MD5 格式
    if stored.startswith("md5:"):
        return hashlib.md5(password.encode()).hexdigest() == stored[4:]
    salt, h = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def get_user_role(username: str) -> str:
    return "reviewer" if username.upper() in settings.reviewer_usernames else "engineering"


def create_token(user_id: int, username: str, tenant_code: str, tenant_name: str, display_name: str = "", is_admin: bool = False) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "tenant_id": tenant_code,
        "tenant_name": tenant_name,
        "display_name": display_name or username,
        "is_admin": is_admin,
        "role": get_user_role(username),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> UserContext:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        return UserContext(
            user_id=payload["user_id"],
            username=payload["username"],
            tenant_id=payload["tenant_id"],
            tenant_name=payload["tenant_name"],
            display_name=payload.get("display_name", payload["username"]),
            is_admin=payload.get("is_admin", False),
            role=payload.get("role", get_user_role(payload["username"])),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的认证令牌")


def get_current_user(authorization: str = Header(...)) -> UserContext:
    """FastAPI 依赖：从 Authorization: Bearer <token> 解析当前用户"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请提供 Bearer Token")
    return decode_token(authorization[7:])
