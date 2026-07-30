from sqlalchemy.orm import Session

from api.models.holding import Holding


class HoldingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self):
        return (
            self.db.query(Holding)
            .order_by(Holding.symbol)
            .all()
        )

    def get_by_symbol(self, symbol: str):
        return (
            self.db.query(Holding)
            .filter(Holding.symbol == symbol)
            .first()
        )

    def create(self, holding: Holding):
        self.db.add(holding)
        self.db.commit()
        self.db.refresh(holding)
        return holding
