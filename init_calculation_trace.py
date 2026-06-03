"""Create quotation calculation trace table."""

from app.database import Base, engine
from app.models.calculation_trace import QuotationCalculationTrace


def main():
    Base.metadata.create_all(engine, tables=[QuotationCalculationTrace.__table__])
    print("quotation calculation trace table: ready")


if __name__ == "__main__":
    main()

