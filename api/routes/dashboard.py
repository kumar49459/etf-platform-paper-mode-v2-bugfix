from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.database.database import get_db
from api.services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    service = DashboardService(db)
    return service.get_summary()
