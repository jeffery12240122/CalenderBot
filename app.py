# -*- coding: utf-8 -*-
"""
LINE 行事曆機器人
- 新增事件（時間、地點、邀請對象）
- 總攬查看
- 到點提醒
"""
import os
import re
from datetime import datetime, timedelta

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction,
    PostbackEvent, PostbackAction
)

import config
import database

app = Flask(__name__)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

# ---------- 指令說明 ----------
HELP_TEXT = """📅 行事曆指令：

【新增事件】
輸入：新增 開始日期 開始時間 結束時間 事件名稱
可加：地點:xxx 邀請:名字1,名字2

例：新增 2025-03-15 14:00 16:00 團隊會議 地點:會議室A 邀請:小明,小華

【總攬】
輸入：總攬 或 行事曆
可加日期：總攬 2025-03-15 或 總攬 2025-03-15 2025-03-20

【刪除】
輸入：刪除 事件編號

輸入 說明 可再次顯示此說明。"""

# 日期解析：支援 2025-03-15、明天、今天
def parse_date(s: str) -> datetime:
    s = s.strip()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "今天":
        return today
    if s == "明天":
        return today + timedelta(days=1)
    if s == "後天":
        return today + timedelta(days=2)
    # 嘗試 YYYY-MM-DD
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%m/%d")
    except ValueError:
        pass
    raise ValueError(f"無法辨識的日期: {s}")

def parse_time(s: str, base_date: datetime) -> datetime:
    """解析時間字串 (HH:MM 或 H:MM)，結合 base_date 回傳 datetime"""
    s = s.strip()
    if not re.match(r"^\d{1,2}:\d{2}$", s):
        raise ValueError(f"時間格式請用 HH:MM: {s}")
    h, m = map(int, s.split(":"))
    return base_date.replace(hour=h, minute=m, second=0, microsecond=0)

def format_event(e: dict) -> str:
    loc = f"\n    📍 {e['location']}" if e.get("location") else ""
    parts = e.get("participants") or ""
    part_line = f"\n    👥 邀請: {parts}" if parts else ""
    return f"#{e['id']} {e['title']}\n    🕐 {e['start_time']} ~ {e['end_time']}{loc}{part_line}"

def get_main_quick_reply():
    return QuickReply(
        items=[
            QuickReplyButton(action=MessageAction(label="📅 總攬", text="總攬")),
            QuickReplyButton(action=MessageAction(label="➕ 新增事件", text="新增")),
            QuickReplyButton(action=MessageAction(label="❓ 說明", text="說明")),
        ]
    )

# ---------- 新增事件解析 ----------
def try_add_event(user_id: str, text: str) -> str:
    """解析「新增 日期 開始時間 結束時間 標題 [地點:xxx] [邀請:a,b]」"""
    parts = text.split(maxsplit=5)  # 最多切 6 段：新增 date start end title rest
    if len(parts) < 5:
        return "格式：新增 日期 開始時間 結束時間 事件名稱\n例：新增 2025-03-15 14:00 16:00 開會 地點:會議室 邀請:小明"
    _, date_s, start_s, end_s, title = parts[0], parts[1], parts[2], parts[3], parts[4]
    rest = parts[5] if len(parts) > 5 else ""
    location = ""
    participants = ""
    for seg in rest.split():
        if seg.startswith("地點:"):
            location = seg[3:].strip()
        elif seg.startswith("邀請:"):
            participants = seg[3:].strip()
    try:
        base = parse_date(date_s)
        start_dt = parse_time(start_s, base)
        end_dt = parse_time(end_s, base)
        if end_dt <= start_dt:
            return "結束時間必須晚於開始時間。"
        start_str = start_dt.strftime("%Y-%m-%d %H:%M")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        return f"日期或時間格式錯誤：{e}"
    eid = database.add_event(user_id, title, start_str, end_str, location, participants)
    return f"✅ 已新增事件 #{eid}\n{title}\n{start_str} ~ {end_str}" + (f"\n📍 {location}" if location else "") + (f"\n👥 邀請: {participants}" if participants else "")

# ---------- 總攬 ----------
def do_overview(user_id: str, text: str) -> str:
    """總攬 [開始日期] [結束日期]"""
    parts = text.split()
    from_date = None
    to_date = None
    if len(parts) >= 2:
        try:
            d = parse_date(parts[1])
            from_date = d.strftime("%Y-%m-%d 00:00")
            to_date = (d + timedelta(days=1)).strftime("%Y-%m-%d 23:59")
        except ValueError:
            pass
    if len(parts) >= 3:
        try:
            d2 = parse_date(parts[2])
            to_date = (d2 + timedelta(days=1)).strftime("%Y-%m-%d 23:59")
        except ValueError:
            pass
    events = database.get_events_by_user(user_id, from_date, to_date)
    if not events:
        range_hint = f"（{from_date or '全部'} ~ {to_date or '全部'}）" if from_date else ""
        return f"📅 目前沒有排程事件{range_hint}。\n輸入「新增」開頭可新增事件。"
    lines = ["📅 行事曆總攬\n"]
    for e in events:
        lines.append(format_event(e))
    return "\n".join(lines)

# ---------- 刪除 ----------
def try_delete_event(user_id: str, text: str) -> str:
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return "請輸入：刪除 事件編號（例：刪除 3）"
    eid = int(parts[1])
    if database.delete_event(eid, user_id):
        return f"✅ 已刪除事件 #{eid}"
    return f"找不到事件 #{eid} 或您沒有權限刪除。"

# ---------- Webhook ----------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()
    reply = None

    if not text:
        reply = TextSendMessage(text=HELP_TEXT, quick_reply=get_main_quick_reply())
    elif text == "說明" or text == "幫助" or text == "help":
        reply = TextSendMessage(text=HELP_TEXT, quick_reply=get_main_quick_reply())
    elif text == "總攬" or text == "行事曆":
        reply = TextSendMessage(text=do_overview(user_id, "總攬"), quick_reply=get_main_quick_reply())
    elif text.startswith("總攬 "):
        reply = TextSendMessage(text=do_overview(user_id, text), quick_reply=get_main_quick_reply())
    elif text == "新增":
        reply = TextSendMessage(
            text="請輸入：新增 日期 開始時間 結束時間 事件名稱\n可加：地點:xxx 邀請:名字1,名字2\n例：新增 2025-03-15 14:00 16:00 開會 地點:會議室A 邀請:小明,小華",
            quick_reply=get_main_quick_reply()
        )
    elif text.startswith("新增 "):
        reply = TextSendMessage(text=try_add_event(user_id, text), quick_reply=get_main_quick_reply())
    elif text.startswith("刪除 "):
        reply = TextSendMessage(text=try_delete_event(user_id, text), quick_reply=get_main_quick_reply())
    else:
        reply = TextSendMessage(text=HELP_TEXT, quick_reply=get_main_quick_reply())

    if reply:
        line_bot_api.reply_message(event.reply_token, reply)

# ---------- 排程提醒 ----------
def send_reminders():
    """檢查即將開始的事件並發送提醒（可被 APScheduler 呼叫）"""
    from linebot.models import TextSendMessage
    now = datetime.now()
    window_end = now + timedelta(minutes=15)
    window_start_s = now.strftime("%Y-%m-%d %H:%M")
    window_end_s = window_end.strftime("%Y-%m-%d %H:%M")
    for user_id in database.get_all_user_ids():
        events = database.get_events_by_user(user_id, window_start_s, window_end_s)
        for e in events:
            msg = f"⏰ 提醒：{e['title']}\n時間：{e['start_time']} ~ {e['end_time']}"
            if e.get("location"):
                msg += f"\n📍 {e['location']}"
            if e.get("participants"):
                msg += f"\n👥 {e['participants']}"
            try:
                line_bot_api.push_message(user_id, TextSendMessage(text=msg))
            except Exception:
                pass

# ---------- 啟動時初始化 DB 與排程（本機 python app.py 或 Render gunicorn 都會執行）----------
def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_reminders, "interval", minutes=5)
    scheduler.start()

# 匯入時就初始化，讓 gunicorn 啟動時也會建 DB、跑排程
database.init_db()
start_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
