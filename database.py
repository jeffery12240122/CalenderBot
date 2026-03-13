# -*- coding: utf-8 -*-
"""行事曆事件資料庫 - 2.0"""
import sqlite3
import json
from datetime import datetime
from typing import Optional, List
import config

def get_connection():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """建立事件表與提醒紀錄表（含 2.0 欄位）"""
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
            category TEXT,
            reminder_minutes TEXT,
            recurrence_type TEXT,
            recurrence_detail TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 2.0: 多筆提醒已發送紀錄（event_id, offset_minutes 唯一）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminder_log (
            event_id INTEGER NOT NULL,
            offset_minutes INTEGER NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (event_id, offset_minutes),
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)
    conn.commit()
    # 為舊表補欄位（若不存在）
    for col, typ in [
        ("category", "TEXT"), ("reminder_minutes", "TEXT"), ("recurrence_type", "TEXT"), ("recurrence_detail", "TEXT")
    ]:
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    conn.close()

def add_event(user_id: str, title: str, start_time: str, end_time: str,
              location: str = "", participants: str = "", category: str = "",
              reminder_minutes: str = "[15]", recurrence_type: str = "", recurrence_detail: str = "") -> int:
    """新增事件，回傳 event id。reminder_minutes 為 JSON 陣列字串，如 "[15,60,1440]"。"""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO events (user_id, title, start_time, end_time, location, participants,
           category, reminder_minutes, recurrence_type, recurrence_detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, title, start_time, end_time, location or "", participants or "",
         category or "", reminder_minutes or "[15]", recurrence_type or "", recurrence_detail or "")
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid

def update_event(event_id: int, user_id: str, **kwargs) -> bool:
    """依 id 更新事件，僅更新傳入的欄位。"""
    allowed = {"title", "start_time", "end_time", "location", "participants",
               "category", "reminder_minutes", "recurrence_type", "recurrence_detail"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return True
    conn = get_connection()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [event_id, user_id]
    cur = conn.execute(
        f"UPDATE events SET {set_clause} WHERE id = ? AND user_id = ?",
        params
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok

def get_events_by_user(user_id: str, from_date: Optional[str] = None, to_date: Optional[str] = None,
                       category: Optional[str] = None) -> List[dict]:
    """取得使用者的所有事件，可選日期區間與分類。"""
    conn = get_connection()
    sql = "SELECT * FROM events WHERE user_id = ?"
    params = [user_id]
    if from_date and to_date:
        sql += " AND start_time >= ? AND start_time < ?"
        params.extend([from_date, to_date])
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY start_time"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_events_overlap(user_id: str, start_time: str, end_time: str, exclude_event_id: Optional[int] = None) -> List[dict]:
    """取得與 [start_time, end_time] 時間重疊的事件。重疊條件：既有 start_time < 新 end_time 且 既有 end_time > 新 start_time。"""
    conn = get_connection()
    sql = "SELECT * FROM events WHERE user_id = ? AND start_time < ? AND end_time > ?"
    params = [user_id, end_time, start_time]
    if exclude_event_id is not None:
        sql += " AND id != ?"
        params.append(exclude_event_id)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_upcoming_events(user_id: str, from_time: str):
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM events WHERE user_id = ? AND start_time >= ?
           ORDER BY start_time LIMIT 100""",
        (user_id, from_time)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_event_by_id(event_id: int, user_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM events WHERE id = ? AND user_id = ?",
        (event_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_event(event_id: int, user_id: str) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    conn.commit()
    deleted = cur.rowcount > 0
    if deleted:
        conn.execute("DELETE FROM reminder_log WHERE event_id = ?", (event_id,))
        conn.commit()
    conn.close()
    return deleted

def get_all_user_ids():
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT user_id FROM events").fetchall()
    conn.close()
    return [r[0] for r in rows]

# ---------- 多筆提醒：紀錄已發送 ----------
def reminder_was_sent(event_id: int, offset_minutes: int) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM reminder_log WHERE event_id = ? AND offset_minutes = ?",
        (event_id, offset_minutes)
    ).fetchone()
    conn.close()
    return row is not None

def mark_reminder_sent(event_id: int, offset_minutes: int):
    conn = get_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO reminder_log (event_id, offset_minutes, sent_at) VALUES (?, ?, ?)",
        (event_id, offset_minutes, now)
    )
    conn.commit()
    conn.close()
