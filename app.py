import os
import csv
import io
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ------------------------------------------------------------------------------
# Конфигурация базы данных (Поддержка Supabase Pooler и SQLite Fallback)
# ------------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./finpulse_prod.db")

# Автоматическое приведение postgres:// к postgresql:// для Render
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_kwargs: Dict[str, Any] = {}
if "sqlite" in DATABASE_URL:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif "pooler.supabase" in DATABASE_URL or ":6543" in DATABASE_URL:
    # Отключаем prepared statements для совместимости с Supavisor Transaction Pooler
    engine_kwargs["connect_args"] = {"prepare_threshold": None}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ------------------------------------------------------------------------------
# SQLAlchemy ORM Модели
# ------------------------------------------------------------------------------
class TransactionDB(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String(20), nullable=False)  # 'income' или 'expense'
    category = Column(String(100), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

class CategoryBudgetDB(Base):
    __tablename__ = "category_budgets"
    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(100), unique=True, nullable=False, index=True)
    allocated_amount = Column(Float, default=0.0)

class GoalDB(Base):
    __tablename__ = "goals"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)

class SubscriptionDB(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False)
    billing_day = Column(Integer, nullable=False)  # День месяца (1-31)
    category = Column(String(100), default="Подписки")

Base.metadata.create_all(bind=engine)

# ------------------------------------------------------------------------------
# Pydantic Валидаторы
# ------------------------------------------------------------------------------
class TransactionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    amount: float = Field(..., gt=0)
    type: str = Field(..., pattern="^(income|expense)$")
    category: str = Field(..., min_length=1, max_length=100)

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
    category: str = Field(..., min_length=1, max_length=100)
    allocated_amount: float = Field(..., ge=0)

class BudgetResponse(BudgetCreate):
    id: int
    class Config:
        from_attributes = True

class GoalCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    target_amount: float = Field(..., gt=0)
    current_amount: float = Field(default=0.0, ge=0)

class GoalDeposit(BaseModel):
    amount: float = Field(..., gt=0)

class GoalResponse(GoalCreate):
    id: int
    class Config:
        from_attributes = True

class SubscriptionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    amount: float = Field(..., gt=0)
    billing_day: int = Field(..., ge=1, le=31)
    category: str = Field(default="Подписки")

class SubscriptionResponse(SubscriptionCreate):
    id: int
    class Config:
        from_attributes = True

# ------------------------------------------------------------------------------
# FastAPI Приложение
# ------------------------------------------------------------------------------
app = FastAPI(
    title="FinPulse SaaS Enterprise",
    description="Production-grade family financial manager with multi-balance accounting",
    version="2.0.0"
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Главная страница ---
@app.get("/", include_in_schema=False)
def read_root():
    return FileResponse("index.html")

# --- REST API: Транзакции ---
@app.get("/api/transactions", response_model=List[TransactionResponse])
def get_transactions(db: Session = Depends(get_db)):
    return db.query(TransactionDB).order_by(TransactionDB.created_at.desc()).all()

@app.post("/api/transactions", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(tx: TransactionCreate, db: Session = Depends(get_db)):
    cat_clean = tx.category.strip()
    db_tx = TransactionDB(
        title=tx.title.strip(),
        amount=tx.amount,
        type=tx.type,
        category=cat_clean
    )
    db.add(db_tx)
    
    # Автосоздание бюджетной категории для расхода, если её не было
    if tx.type == "expense":
        existing_budget = db.query(CategoryBudgetDB).filter_by(category=cat_clean).first()
        if not existing_budget:
            db.add(CategoryBudgetDB(category=cat_clean, allocated_amount=0.0))
            
    db.commit()
    db.refresh(db_tx)
    return db_tx

@app.put("/api/transactions/{tx_id}", response_model=TransactionResponse)
def update_transaction(tx_id: int, tx_data: TransactionUpdate, db: Session = Depends(get_db)):
    tx = db.query(TransactionDB).filter(TransactionDB.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    
    if tx_data.title is not None: tx.title = tx_data.title.strip()
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
    return {"status": "success", "message": "Транзакция успешно удалена"}

# --- REST API: Бюджеты ---
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
    b = db.query(CategoryBudgetDB).filter_by(category=cat_name.strip()).first()
    if b:
        db.delete(b)
        db.commit()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Категория не найдена")

# --- REST API: Копилки и Цели ---
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

# --- REST API: Подписки ---
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

# --- REST API: Полный финансовый расчет и сводка остатков ---
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

    # Точные финансовые остатки
    net_balance = total_income - total_expense
    unallocated_income = max(0.0, total_income - total_allocated)

    # Детальный расчет остатков по категориям
    category_expenses: Dict[str, float] = {}
    for t in transactions:
        if t.type == "expense":
            cat = t.category.strip()
            category_expenses[cat] = category_expenses.get(cat, 0.0) + t.amount

    category_summaries = []
    all_categories = set(list(category_expenses.keys()) + [b.category for b in budgets])

    for cat in all_categories:
        b_obj = next((b for b in budgets if b.category == cat), None)
        allocated = b_obj.allocated_amount if b_obj else 0.0
        spent = category_expenses.get(cat, 0.0)
        remaining = allocated - spent
        percent_spent = round((spent / allocated * 100), 1) if allocated > 0 else (100.0 if spent > 0 else 0.0)

        category_summaries.append({
            "category": cat,
            "allocated": allocated,
            "spent": spent,
            "remaining": remaining,  # Положительный = остаток лимита, отрицательный = перерасход
            "percent_spent": percent_spent,
            "is_overbudget": spent > allocated and allocated > 0
        })

    # Оценка финансовой устойчивости (0-100%)
    savings_rate = round(((net_balance) / total_income * 100), 1) if total_income > 0 else 0.0
    health_score = min(100, max(0, int(savings_rate * 1.5 + (20 if total_allocated > 0 else 0))))

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "net_balance": net_balance,                          # Общий остаток
        "total_allocated": total_allocated,
        "unallocated_income": unallocated_income,            # Нераспределенный остаток
        "total_saved_goals": total_saved_goals,
        "monthly_subscriptions": monthly_subs,
        "savings_rate": savings_rate,
        "health_score": health_score,
        "categories": category_summaries
    }

# --- Экспорт отчета в CSV ---
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
        headers={"Content-Disposition": "attachment; filename=FinPulse_SaaS_Report.csv"}
    )
