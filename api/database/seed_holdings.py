from sqlalchemy.orm import Session
from api.models.holding import Holding

def seed_holdings(db: Session):
    if db.query(Holding).count() > 0:
        return

    db.add_all([
        Holding(
            symbol="NIFTYBEES",
            name="Nippon India ETF Nifty 50 BeES",
            sector="Broad Market",
            country="India",
            units=420,
            avg_cost=231.40,
            current_price=251.85,
            previous_close=249.60,
            dividend_yield=1.10,
        ),
        Holding(
            symbol="GOLDBEES",
            name="Nippon India ETF Gold BeES",
            sector="Gold",
            country="India",
            units=1850,
            avg_cost=52.10,
            current_price=61.32,
            previous_close=60.98,
            dividend_yield=0.00,
        ),
        Holding(
            symbol="BANKBEES",
            name="Nippon India ETF Bank BeES",
            sector="Banking",
            country="India",
            units=180,
            avg_cost=468.20,
            current_price=512.70,
            previous_close=508.15,
            dividend_yield=0.80,
        ),
    ])

    db.commit()
