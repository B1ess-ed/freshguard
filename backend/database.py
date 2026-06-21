from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SEED_INGREDIENTS = [
    ("鲜牛奶", "乳制品", 0.22, "盒", 1, "", 0.25),
    ("生菜", "蔬菜", 1, "颗", 1, "建议尽快食用", None),
    ("鸡蛋", "蛋类", 8, "个", 9, "", 4),
    ("蓝莓", "水果", 1, "盒", 4, "", None),
    ("番茄", "蔬菜", 3, "个", 5, "", 1),
    ("苹果", "水果", 4, "个", 12, "", 2),
    ("芝士", "乳制品", 180, "克", 6, "", 50),
    ("西兰花", "蔬菜", 1, "颗", 3, "", None),
    ("橙汁", "饮品", 0.68, "瓶", 7, "", 0.2),
    ("酸奶", "乳制品", 2, "杯", 7, "", 1),
    ("胡萝卜", "蔬菜", 2, "根", 8, "", 1),
    ("柠檬", "水果", 2, "个", 14, "", 1),
]


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    quantity REAL NOT NULL CHECK (quantity >= 0),
                    unit TEXT NOT NULL,
                    expiration_date TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    low_stock_threshold REAL CHECK (
                        low_stock_threshold IS NULL OR low_stock_threshold >= 0
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            count = connection.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0]
            if count == 0:
                now = utc_now()
                today = date.today()
                seed_rows = [
                    (*item[:4], (today + timedelta(days=item[4])).isoformat(), *item[5:])
                    for item in SEED_INGREDIENTS
                ]
                connection.executemany(
                    """
                    INSERT INTO ingredients (
                        name, category, quantity, unit, expiration_date, notes,
                        low_stock_threshold, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [(*item, now, now) for item in seed_rows],
                )

    def list_ingredients(self, category: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ingredients"
        params: list[Any] = []
        if category:
            query += " WHERE category = ?"
            params.append(category)
        query += " ORDER BY updated_at DESC, id DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [serialize(row) for row in rows]

    def get_ingredient(self, ingredient_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)
            ).fetchone()
        return serialize(row) if row else None

    def create_ingredient(self, values: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ingredients (
                    name, category, quantity, unit, expiration_date, notes,
                    low_stock_threshold, created_at, updated_at
                ) VALUES (:name, :category, :quantity, :unit, :expiration_date,
                          :notes, :low_stock_threshold, :created_at, :updated_at)
                """,
                {**values, "created_at": now, "updated_at": now},
            )
            ingredient_id = cursor.lastrowid
        return self.get_ingredient(ingredient_id)  # type: ignore[arg-type, return-value]

    def update_ingredient(
        self, ingredient_id: int, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ingredients SET
                    name = :name,
                    category = :category,
                    quantity = :quantity,
                    unit = :unit,
                    expiration_date = :expiration_date,
                    notes = :notes,
                    low_stock_threshold = :low_stock_threshold,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                {**values, "updated_at": utc_now(), "id": ingredient_id},
            )
            if cursor.rowcount == 0:
                return None
        return self.get_ingredient(ingredient_id)

    def delete_ingredient(self, ingredient_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ingredients WHERE id = ?", (ingredient_id,)
            )
        return cursor.rowcount > 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ingredient_status(
    quantity: float,
    threshold: float | None,
    expiration_date: str | None,
    today: date | None = None,
) -> str:
    if threshold is not None and quantity <= threshold:
        return "low"
    if expiration_date:
        expiry = date.fromisoformat(expiration_date)
        current = today or date.today()
        if expiry < current:
            return "expired"
        if expiry <= current + timedelta(days=3):
            return "soon"
    return "fresh"


def serialize(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["status"] = ingredient_status(
        item["quantity"], item["low_stock_threshold"], item["expiration_date"]
    )
    return item
