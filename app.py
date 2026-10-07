from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from pydantic import BaseModel
from datetime import datetime

DATABASE_URL = "sqlite:///./budget.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class TransactionDB(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # "income" или "expense"
    category = Column(String, default="Общее")
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI()

# Pydantic схема для валидации входящих данных
class TransactionCreate(BaseModel):
    title: str
    amount: float
    type: str  # 'income' или 'expense'
    category: str = "Общее"

@app.get("/", response_class=HTMLResponse)
def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/summary")
def get_summary():
    db = SessionLocal()
    transactions = db.query(TransactionDB).all()
    
    total_income = sum(t.amount for t in transactions if t.type == "income")
    total_expense = sum(t.amount for t in transactions if t.type == "expense")
    balance = total_income - total_expense
    
    db.close()
    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance
    }

@app.get("/api/transactions")
def get_transactions():
    db = SessionLocal()
    transactions = db.query(TransactionDB).order_by(TransactionDB.created_at.desc()).all()
    db.close()
    return transactions

@app.post("/api/transactions")
def add_transaction(item: TransactionCreate):
    if item.type not in ["income", "expense"]:
        raise HTTPException(status_code=400, detail="Неверный тип операции")
    
    db = SessionLocal()
    db_item = TransactionDB(
        title=item.title,
        amount=item.amount,
        type=item.type,
        category=item.category
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    db.close()
    return db_item

@app.delete("/api/transactions/{item_id}")
def delete_transaction(item_id: int):
    db = SessionLocal()
    item = db.query(TransactionDB).filter(TransactionDB.id == item_id).first()
    if not item:
        db.close()
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(item)
    db.commit()
    db.close()
    return {"status": "success"}