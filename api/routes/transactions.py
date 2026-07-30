from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.database.database import get_db
from api.services.transaction_service import TransactionService

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])

@router.get("")
def get_transactions(db: Session = Depends(get_db)):
    service = TransactionService(db)
    return service.get_all_transactions()
