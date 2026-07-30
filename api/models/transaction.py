from sqlalchemy import Column, Integer, String, Float
from api.database.database import Base

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    units = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    broker = Column(String, nullable=False)
