"""Create PVC material-price audit storage and initialize prices from legacy BOM details."""

from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.models.pvc_material_price import PVCMaterialPriceLog
from app.services.pvc_material_price_service import initialize_from_bom


def main():
    Base.metadata.create_all(engine, tables=[PVCMaterialPriceLog.__table__])
    with engine.begin() as connection:
        connection.execute(text("""
            IF NOT EXISTS (
                SELECT 1 FROM sys.indexes
                WHERE name = 'ix_PVC_MaterialPrice_PRD_NO_HSYF'
                  AND object_id = OBJECT_ID('dbo.PVC_MaterialPrice')
            )
            CREATE INDEX ix_PVC_MaterialPrice_PRD_NO_HSYF
                ON dbo.PVC_MaterialPrice (PRD_NO, HSYF DESC)
        """))
    print("dbo.PVC_MaterialPrice_Log and business index: ready")

    db = SessionLocal()
    try:
        result = initialize_from_bom(db)
        print(f"initialize complete: created={result['created']} skipped={result['skipped']}")
        for item in result["missing_price_items"]:
            print(f"missing price: {item['PRD_NO']} | {item['NAME']} | {item['UT']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

