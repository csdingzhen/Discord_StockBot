"""
db.py — shared SQLite connection helper.

One file-based database for persisted bot state (flash-news history, and
future module data such as options-anomaly baselines). Lives in data/bot.db,
created on first use.
"""
import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
