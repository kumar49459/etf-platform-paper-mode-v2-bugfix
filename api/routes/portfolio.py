from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.database.database import get_db
from api.services.holding_service import HoldingService

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])

@router.get("")
def get_portfolio(db: Session = Depends(get_db)):
    service = HoldingService(db)
    return service.get_all_holdings()
