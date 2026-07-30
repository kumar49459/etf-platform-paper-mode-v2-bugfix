from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database.database import Base, engine, SessionLocal
from api.database.seed import seed_transactions
from api.database.seed_holdings import seed_holdings

from api.models.transaction import Transaction
from api.models.holding import Holding

from api.routes.dashboard import router as dashboard_router
from api.routes.portfolio import router as portfolio_router
from api.routes.transactions import router as transactions_router

# Create all database tables
Base.metadata.create_all(bind=engine)

# Seed initial data
db = SessionLocal()
try:
    seed_transactions(db)
    seed_holdings(db)
finally:
    db.close()

app = FastAPI(title="ETF Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
app.include_router(portfolio_router)
app.include_router(transactions_router)
