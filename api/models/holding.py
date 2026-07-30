from sqlalchemy import Column, Integer, String, Float
from api.database.database import Base

class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    sector = Column(String, nullable=False)
    country = Column(String, nullable=False)
    units = Column(Float, nullable=False)
    avg_cost = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    previous_close = Column(Float, nullable=False)
    dividend_yield = Column(Float, nullable=False)
