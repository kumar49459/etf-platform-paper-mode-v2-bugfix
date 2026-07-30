from sqlalchemy.orm import Session
from api.models.transaction import Transaction

def seed_transactions(db: Session):
    if db.query(Transaction).count() > 0:
        return

    db.add_all([
        Transaction(
            date="2026-07-25",
            symbol="NIFTYBEES",
            type="BUY",
            status="Completed",
            units=120,
            price=248.50,
            amount=29820.00,
            broker="Zerodha",
        ),
        Transaction(
            date="2026-07-21",
            symbol="GOLDBEES",
            type="BUY",
            status="Completed",
            units=500,
            price=60.20,
            amount=30100.00,
            broker="Zerodha",
        ),
        Transaction(
            date="2026-07-18",
            symbol="BANKBEES",
            type="BUY",
            status="Completed",
            units=50,
            price=505.00,
            amount=25250.00,
            broker="Zerodha",
        ),
    ])

    db.commit()
