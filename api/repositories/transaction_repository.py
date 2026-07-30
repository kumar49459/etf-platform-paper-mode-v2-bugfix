from sqlalchemy.orm import Session

from api.models.transaction import Transaction


class TransactionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self):
        return (
            self.db.query(Transaction)
            .order_by(Transaction.date.desc())
            .all()
        )

    def get_by_id(self, transaction_id: int):
        return (
            self.db.query(Transaction)
            .filter(Transaction.id == transaction_id)
            .first()
        )

    def create(self, transaction: Transaction):
        self.db.add(transaction)
        self.db.commit()
        self.db.refresh(transaction)
        return transaction
