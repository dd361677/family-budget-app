import os
from typing import List
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(
    DATABASE_URL,
    connect_args={"prepare_threshold": None} if DATABASE_URL and "pooler.supabase" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Модели БД ---

class CategoryBudgetDB(Base):
    __tablename__ = "category_budgets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, unique=True, nullable=False, index=True)
    allocated_amount = Column(Float, default=0.0)  # Выделено из дохода

class TransactionDB(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # 'income' или 'expense'
    category = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Конвертный Семейный Бюджет")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Pydantic Схемы ---

class TransactionCreate(BaseModel):
    title: str
    amount: float
    type: str
    category: str

class TransactionResponse(TransactionCreate):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True

class BudgetCreate(BaseModel):
    category: str
    allocated_amount: float

class BudgetResponse(BudgetCreate):
    id: int
    class Config:
        from_attributes = True

# --- API ---

@app.get("/")
def read_root():
    return FileResponse("index.html")

@app.get("/api/transactions", response_model=List[TransactionResponse])
def get_transactions(db: Session = Depends(get_db)):
    return db.query(TransactionDB).order_by(TransactionDB.created_at.desc()).all()

@app.post("/api/transactions", response_model=TransactionResponse)
def create_transaction(tx: TransactionCreate, db: Session = Depends(get_db)):
    db_tx = TransactionDB(**tx.model_dump())
    db.add(db_tx)
    
    # Автосоздание категории без лимита, если ее еще нет
    if tx.type == "expense":
        existing_budget = db.query(CategoryBudgetDB).filter_by(category=tx.category).first()
        if not existing_budget:
            db.add(CategoryBudgetDB(category=tx.category, allocated_amount=0.0))
            
    db.commit()
    db.refresh(db_tx)
    return db_tx

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.query(TransactionDB).filter(TransactionDB.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(tx)
    db.commit()
    return {"status": "success"}

@app.get("/api/budgets", response_model=List[BudgetResponse])
def get_budgets(db: Session = Depends(get_db)):
    return db.query(CategoryBudgetDB).all()

@app.post("/api/budgets", response_model=BudgetResponse)
def set_budget(budget: BudgetCreate, db: Session = Depends(get_db)):
    existing = db.query(CategoryBudgetDB).filter_by(category=budget.category).first()
    if existing:
        existing.allocated_amount = budget.allocated_amount
        db.commit()
        db.refresh(existing)
        return existing
    else:
        new_budget = CategoryBudgetDB(**budget.model_dump())
        db.add(new_budget)
        db.commit()
        db.refresh(new_budget)
        return new_budget

@app.get("/api/summary")
def get_summary(db: Session = Depends(get_db)):
    transactions = db.query(TransactionDB).all()
    budgets = db.query(CategoryBudgetDB).all()

    total_income = sum(t.amount for t in transactions if t.type == "income")
    total_expense = sum(t.amount for t in transactions if t.type == "expense")
    total_allocated = sum(b.allocated_amount for b in budgets)
    
    # Нераспределенный доход
    unallocated_income = total_income - total_allocated

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "total_allocated": total_allocated,
        "unallocated_income": unallocated_income,
        "real_balance": total_income - total_expense
    }
