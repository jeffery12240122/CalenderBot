# -*- coding: utf-8 -*-
"""行事曆事件資料庫"""
import sqlite3
import json
from datetime import datetime
from typing import Optional
import config

def get_connection():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """建立事件表"""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            location TEXT,
            participants TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_event(user_id: str, title: str, start_time: str, end_time: str,
              location: str = "", participants: str = "") -> int:
    """新增事件，回傳 event id"""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO events (user_id, title, start_time, end_time, location, participants)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, title, start_time, end_time, location or "", participants or "")
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid

def get_events_by_user(user_id: str, from_date: Optional[str] = None, to_date: Optional[str] = None):
    """取得使用者的所有事件，可選日期區間（依 start_time）"""
    conn = get_connection()
    if from_date and to_date:
        rows = conn.execute(
            """SELECT * FROM events WHERE user_id = ? AND start_time >= ? AND start_time < ?
               ORDER BY start_time""",
            (user_id, from_date, to_date)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE user_id = ? ORDER BY start_time",
            (user_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_upcoming_events(user_id: str, from_time: str):
    """取得某時間之後的事件（用於提醒）"""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM events WHERE user_id = ? AND start_time >= ?
           ORDER BY start_time LIMIT 100""",
        (user_id, from_time)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_event_by_id(event_id: int, user_id: str):
    """依 id 取得單一事件"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM events WHERE id = ? AND user_id = ?",
        (event_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_event(event_id: int, user_id: str) -> bool:
    """刪除事件"""
    conn = get_connection()
    cur = conn.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted

def get_all_user_ids():
    """取得所有有事件的 user_id（用於排程提醒）"""
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT user_id FROM events").fetchall()
    conn.close()
    return [r[0] for r in rows]
