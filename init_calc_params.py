"""Create quotation calculation-parameter tables."""

from app.database import Base, engine
from app.models.calc_param import QuotationCalcParam


def main():
    Base.metadata.create_all(engine, tables=[QuotationCalcParam.__table__])
    print("quotation calculation parameter table: ready")


if __name__ == "__main__":
    main()

