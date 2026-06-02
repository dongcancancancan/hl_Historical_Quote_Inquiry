"""Create or update the local reviewer accounts used by the pricing-review desk."""

from sqlalchemy.orm import Session

from app.core.auth import hash_password_md5, verify_password
from app.database import SessionLocal
from app.models.user import Tenant, User


REVIEWER_ACCOUNTS = (
    ("EG253100", "253100", "EG253100"),
    ("EG199214", "199214", "EG199214"),
)


def seed_reviewer_users(db: Session) -> list[str]:
    tenant = db.query(Tenant).filter(Tenant.code == "engineering").first()
    if not tenant:
        raise RuntimeError("Tenant 'engineering' does not exist")

    results = []
    for username, password, display_name in REVIEWER_ACCOUNTS:
        password_hash = hash_password_md5(password)
        user = db.query(User).filter(
            User.tenant_id == tenant.id,
            User.username == username,
        ).first()
        if user:
            user.password_hash = password_hash
            user.display_name = display_name
            action = "updated"
        else:
            user = User(
                tenant_id=tenant.id,
                username=username,
                password_hash=password_hash,
                display_name=display_name,
                is_admin=False,
            )
            db.add(user)
            action = "created"
        if not verify_password(password, password_hash):
            raise RuntimeError(f"MD5 verification failed for {username}")
        results.append(f"{username}: {action}")

    db.commit()
    return results


if __name__ == "__main__":
    session = SessionLocal()
    try:
        for message in seed_reviewer_users(session):
            print(message)
    finally:
        session.close()
