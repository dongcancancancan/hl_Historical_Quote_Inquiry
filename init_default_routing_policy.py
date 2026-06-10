from sqlalchemy import select

from init_routing_tables import main as init_routing_tables_main
from app.database import SessionLocal
from app.models.quotation import QuotationMain
from app.models.user import Tenant
from app.services.routing_policy_service import ensure_default_policy


def _tenant_codes(db) -> list[str]:
    codes = set()

    tenant_rows = db.execute(select(Tenant.code)).all()
    for row in tenant_rows:
        code = str(row[0] or "").strip()
        if code:
            codes.add(code)

    quotation_rows = db.execute(
        select(QuotationMain.tenant_id).distinct().where(QuotationMain.tenant_id.is_not(None))
    ).all()
    for row in quotation_rows:
        code = str(row[0] or "").strip()
        if code:
            codes.add(code)

    return sorted(codes)


def main() -> None:
    init_routing_tables_main()

    db = SessionLocal()
    try:
        tenant_codes = _tenant_codes(db)
        for tenant_code in tenant_codes:
            ensure_default_policy(db, tenant_code, operator="SYSTEM")
        db.commit()
        print(f"default routing policies ready for {len(tenant_codes)} tenants")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
