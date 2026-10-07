import os
import csv
import io
from typing import List, Optional
from datetime import datetime, date
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Date
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Инициализация БД (SQLite по умолчанию)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./finpulse_elite.db")

engine_args = {}
if DATABASE_URL.startswith("sqlite"):
    engine_args["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Модели Таблиц БД ---

class TransactionDB(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # 'income' или 'expense'
    category = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CategoryBudgetDB(Base):
    __tablename__ = "category_budgets"
    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, unique=True, nullable=False)
    allocated_amount = Column(Float, default=0.0)

class GoalDB(Base):
    __tablename__ = "goals"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)

class SubscriptionDB(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    billing_day = Column(Integer, nullable=False)  # День месяца (1-31)
    category = Column(String, default="Подписки")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FinPulse Elite — Financial Management Platform")

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

class TransactionUpdate(BaseModel):
    title: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    category: Optional[str] = None

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

class GoalCreate(BaseModel):
    title: str
    target_amount: float
    current_amount: float = 0.0

class GoalResponse(GoalCreate):
    id: int
    class Config:
        from_attributes = True

class GoalDeposit(BaseModel):
    amount: float

class SubscriptionCreate(BaseModel):
    title: str
    amount: float
    billing_day: int
    category: str = "Подписки"

class SubscriptionResponse(SubscriptionCreate):
    id: int
    class Config:
        from_attributes = True

# --- API Эндпоинты ---

@app.get("/")
def read_root():
    return FileResponse("index.html")

# --- Транзакции ---

@app.get("/api/transactions", response_model=List[TransactionResponse])
def get_transactions(db: Session = Depends(get_db)):
    return db.query(TransactionDB).order_by(TransactionDB.created_at.desc()).all()

@app.post("/api/transactions", response_model=TransactionResponse)
def create_transaction(tx: TransactionCreate, db: Session = Depends(get_db)):
    cat_clean = tx.category.strip()
    db_tx = TransactionDB(
        title=tx.title,
        amount=tx.amount,
        type=tx.type,
        category=cat_clean
    )
    db.add(db_tx)
    
    # Если это расход и категории ещё нет в бюджетах — создадим базовую запись
    if tx.type == "expense":
        existing_b = db.query(CategoryBudgetDB).filter_by(category=cat_clean).first()
        if not existing_b:
            db.add(CategoryBudgetDB(category=cat_clean, allocated_amount=0.0))
            
    db.commit()
    db.refresh(db_tx)
    return db_tx

@app.put("/api/transactions/{tx_id}", response_model=TransactionResponse)
def update_transaction(tx_id: int, tx_data: TransactionUpdate, db: Session = Depends(get_db)):
    tx = db.query(TransactionDB).filter(TransactionDB.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    
    if tx_data.title is not None: tx.title = tx_data.title
    if tx_data.amount is not None: tx.amount = tx_data.amount
    if tx_data.type is not None: tx.type = tx_data.type
    if tx_data.category is not None: tx.category = tx_data.category.strip()
    
    db.commit()
    db.refresh(tx)
    return tx

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.query(TransactionDB).filter(TransactionDB.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    db.delete(tx)
    db.commit()
    return {"status": "success", "message": "Транзакция удалена"}

# --- Бюджеты ---

@app.get("/api/budgets", response_model=List[BudgetResponse])
def get_budgets(db: Session = Depends(get_db)):
    return db.query(CategoryBudgetDB).all()

@app.post("/api/budgets", response_model=BudgetResponse)
def set_budget(budget: BudgetCreate, db: Session = Depends(get_db)):
    cat_clean = budget.category.strip()
    existing = db.query(CategoryBudgetDB).filter_by(category=cat_clean).first()
    if existing:
        existing.allocated_amount = budget.allocated_amount
        db.commit()
        db.refresh(existing)
        return existing
    else:
        new_b = CategoryBudgetDB(category=cat_clean, allocated_amount=budget.allocated_amount)
        db.add(new_b)
        db.commit()
        db.refresh(new_b)
        return new_b

@app.delete("/api/budgets/{cat_name}")
def delete_budget(cat_name: str, db: Session = Depends(get_db)):
    b = db.query(CategoryBudgetDB).filter_by(category=cat_name).first()
    if b:
        db.delete(b)
        db.commit()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Категория не найдена")

# --- Цели ---

@app.get("/api/goals", response_model=List[GoalResponse])
def get_goals(db: Session = Depends(get_db)):
    return db.query(GoalDB).all()

@app.post("/api/goals", response_model=GoalResponse)
def create_goal(goal: GoalCreate, db: Session = Depends(get_db)):
    new_g = GoalDB(**goal.model_dump())
    db.add(new_g)
    db.commit()
    db.refresh(new_g)
    return new_g

@app.post("/api/goals/{goal_id}/deposit", response_model=GoalResponse)
def deposit_goal(goal_id: int, dep: GoalDeposit, db: Session = Depends(get_db)):
    g = db.query(GoalDB).filter(GoalDB.id == goal_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Цель не найдена")
    g.current_amount += dep.amount
    db.commit()
    db.refresh(g)
    return g

@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int, db: Session = Depends(get_db)):
    g = db.query(GoalDB).filter(GoalDB.id == goal_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Цель не найдена")
    db.delete(g)
    db.commit()
    return {"status": "success"}

# --- Регулярные Подписки ---

@app.get("/api/subscriptions", response_model=List[SubscriptionResponse])
def get_subscriptions(db: Session = Depends(get_db)):
    return db.query(SubscriptionDB).all()

@app.post("/api/subscriptions", response_model=SubscriptionResponse)
def create_subscription(sub: SubscriptionCreate, db: Session = Depends(get_db)):
    new_sub = SubscriptionDB(**sub.model_dump())
    db.add(new_sub)
    db.commit()
    db.refresh(new_sub)
    return new_sub

@app.delete("/api/subscriptions/{sub_id}")
def delete_subscription(sub_id: int, db: Session = Depends(get_db)):
    s = db.query(SubscriptionDB).filter(SubscriptionDB.id == sub_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    db.delete(s)
    db.commit()
    return {"status": "success"}

# --- Сводный Отчет ---

@app.get("/api/summary")
def get_summary(db: Session = Depends(get_db)):
    transactions = db.query(TransactionDB).all()
    budgets = db.query(CategoryBudgetDB).all()
    goals = db.query(GoalDB).all()
    subs = db.query(SubscriptionDB).all()

    total_income = sum(t.amount for t in transactions if t.type == "income")
    total_expense = sum(t.amount for t in transactions if t.type == "expense")
    total_allocated = sum(b.allocated_amount for b in budgets)
    total_saved_goals = sum(g.current_amount for g in goals)
    monthly_subs = sum(s.amount for s in subs)

    savings_rate = round(((total_income - total_expense) / total_income * 100), 1) if total_income > 0 else 0
    health_score = min(100, max(0, int(savings_rate * 1.4 + (20 if total_allocated > 0 else 0))))

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "total_allocated": total_allocated,
        "unallocated_income": max(0, total_income - total_allocated),
        "net_balance": total_income - total_expense,
        "total_saved_goals": total_saved_goals,
        "monthly_subscriptions": monthly_subs,
        "savings_rate": savings_rate,
        "health_score": health_score
    }

# --- Экспорт в CSV ---

@app.get("/api/export/csv")
def export_csv(db: Session = Depends(get_db)):
    transactions = db.query(TransactionDB).order_by(TransactionDB.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Название", "Сумма (₸)", "Тип", "Категория", "Дата"])
    
    for t in transactions:
        writer.writerow([t.id, t.title, t.amount, t.type, t.category, t.created_at.strftime("%Y-%m-%d %H:%M:%S")])
        
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=FinPulse_Report.csv"}
    )
