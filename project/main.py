"""
Учёт личных расходов студента — backend на FastAPI.

Хранилище: JSON-файл (data.json) рядом со скриптом.
Без ИИ, без внешних API — всё считается на введённых пользователем данных.
"""

import json
import os
import threading
import uuid
from datetime import date as date_type
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, "data.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
_lock = threading.Lock()


# ---------- Хранилище ----------

def load_data() -> List[dict]:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_data(data: List[dict]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- Модели ----------

class ExpenseIn(BaseModel):
    amount: float
    category: str
    date: str  # формат YYYY-MM-DD
    description: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Сумма должна быть положительным числом")
        return round(v, 2)

    @field_validator("category")
    @classmethod
    def category_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Категория не может быть пустой")
        return v

    @field_validator("date")
    @classmethod
    def date_must_be_valid(cls, v: str) -> str:
        try:
            date_type.fromisoformat(v)
        except ValueError:
            raise ValueError("Дата должна быть в формате YYYY-MM-DD")
        return v

    @field_validator("description")
    @classmethod
    def description_strip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class Expense(ExpenseIn):
    id: str


class BulkIn(BaseModel):
    """Пачка расходов — используется только тест-панелью на фронте
    для проверки производительности на больших объёмах."""
    items: List[ExpenseIn]


def _validate_month_param(month: str) -> None:
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise HTTPException(400, "Параметр month должен быть в формате YYYY-MM")
    try:
        y, m = int(parts[0]), int(parts[1])
        if not (1 <= m <= 12):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Параметр month должен быть в формате YYYY-MM")


# ---------- Приложение ----------

app = FastAPI(title="Student Expense Tracker API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/expenses", response_model=List[Expense])
def get_expenses(month: Optional[str] = Query(None, description="Фильтр YYYY-MM")):
    """Список расходов, опционально отфильтрованный по месяцу."""
    if month:
        _validate_month_param(month)
    data = load_data()
    if month:
        data = [e for e in data if e["date"].startswith(month)]
    data.sort(key=lambda e: e["date"], reverse=True)
    return data


@app.post("/expenses", response_model=Expense, status_code=201)
def add_expense(expense: ExpenseIn):
    """Добавить расход. Валидация суммы/категории/даты — через Pydantic."""
    new_expense = expense.model_dump()
    new_expense["id"] = str(uuid.uuid4())
    with _lock:
        data = load_data()
        data.append(new_expense)
        save_data(data)
    return new_expense


@app.post("/expenses/bulk", response_model=List[Expense], status_code=201)
def add_expenses_bulk(payload: BulkIn):
    """Добавить сразу много расходов (используется тест-панелью на фронте)."""
    added = []
    with _lock:
        data = load_data()
        for item in payload.items:
            e = item.model_dump()
            e["id"] = str(uuid.uuid4())
            data.append(e)
            added.append(e)
        save_data(data)
    return added


@app.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: str):
    """Удалить расход по id."""
    with _lock:
        data = load_data()
        new_data = [e for e in data if e["id"] != expense_id]
        if len(new_data) == len(data):
            raise HTTPException(404, "Расход не найден")
        save_data(new_data)
    return


@app.delete("/expenses", status_code=204)
def clear_expenses():
    """Удалить все расходы разом (кнопка «Очистить все данные» на фронте)."""
    with _lock:
        save_data([])
    return


@app.get("/summary")
def get_summary(month: str = Query(..., description="YYYY-MM, обязателен")):
    """Итог за месяц: общая сумма и разбивка по категориям."""
    _validate_month_param(month)
    data = load_data()
    month_expenses = [e for e in data if e["date"].startswith(month)]

    total = round(sum(e["amount"] for e in month_expenses), 2)

    by_category: dict = {}
    for e in month_expenses:
        by_category[e["category"]] = round(
            by_category.get(e["category"], 0) + e["amount"], 2
        )

    return {
        "month": month,
        "total": total,
        "by_category": by_category,
        "count": len(month_expenses),
    }


# ---------- Фронтенд ----------
# Отдаём index.html и статику из папки static/, чтобы весь прототип
# (фронт + бэк) поднимался одной командой и одним портом.
# Регистрируется ПОСЛЕ api-роутов выше — конкретные пути вроде /expenses
# перехватываются раньше и в статику не попадают.
if os.path.isdir(STATIC_DIR):
    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
