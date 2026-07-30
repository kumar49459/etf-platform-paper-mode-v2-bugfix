from sqlalchemy.orm import Session

from api.repositories.transaction_repository import TransactionRepository


class TransactionService:
    def __init__(self, db: Session):
        self.repository = TransactionRepository(db)

    def get_all_transactions(self):
        transactions = self.repository.get_all()

        return [
            {
                "id": t.id,
                "date": t.date,
                "symbol": t.symbol,
                "type": t.type,
                "status": t.status,
                "units": t.units,
                "price": t.price,
                "amount": t.amount,
                "broker": t.broker,
            }
            for t in transactions
        ]

    def get_transaction(self, transaction_id: int):
        return self.repository.get_by_id(transaction_id)
