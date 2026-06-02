"""Create dbo copper-fee tables and optionally import the current Excel sheet."""

import argparse

from app.database import Base, SessionLocal, engine
from app.models.copper_fee import CopperProcessingFee, CopperProcessingFeeLog
from app.services.copper_fee_service import import_copper_fees


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", help="Excel file to import after creating tables")
    parser.add_argument("--operator", default="SYSTEM")
    args = parser.parse_args()

    Base.metadata.create_all(engine, tables=[
        CopperProcessingFee.__table__,
        CopperProcessingFeeLog.__table__,
    ])
    print("dbo copper fee tables: ready")

    if args.excel:
        db = SessionLocal()
        try:
            result = import_copper_fees(db, args.excel, args.operator)
            print(f"import complete: created={result['created']} updated={result['updated']}")
        finally:
            db.close()


if __name__ == "__main__":
    main()
