# -*- coding: utf-8 -*-
"""
LINE 行事曆機器人 2.0
- 新增/修改/刪除事件、總覽、單一詳情
- 自訂與多筆提醒、衝突偵測、總覽按鈕
- 分類、今日/本週摘要、重複事件
"""
import os
import re
import json
from datetime import datetime, timedelta

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction, PostbackAction,
    PostbackEvent, FlexSendMessage, BubbleContainer, BoxComponent,
    TextComponent, ButtonComponent, SeparatorComponent, CarouselContainer
)

import config
import database

app = Flask(__name__)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

# 衝突時暫存待新增事件
pending_add = {}
# 多步驟流程狀態：user_id -> {"state": str, "data": dict}
user_states = {}

# ---------- 提醒時間對照（分鐘）----------
REMINDER_MAP = {
    "15分": 15, "30分": 30, "1小時": 60, "2小時": 120,
    "1天": 1440, "2天": 2880, "1週": 10080,
}

def parse_reminder(s: str) -> list:
    """解析「提醒:15分,1小時前,1天」-> [15, 60, 1440]，未指定則 [15]。"""
    if not s:
        return [15]
    out = []
    for part in s.replace("，", ",").split(","):
        part = part.strip().replace("前", "").strip()
        if part in REMINDER_MAP:
            out.append(REMINDER_MAP[part])
        else:
            m = re.match(r"^(\d+)(分|小時|天)$", part)
            if m:
                n, unit = int(m.group(1)), m.group(2)
                if unit == "分": out.append(n)
                elif unit == "小時": out.append(n * 60)
                elif unit == "天": out.append(n * 1440)
    return sorted(set(out)) if out else [15]

# ---------- 說明（以按鈕為主，僅地點與自訂名稱需打字）----------
HELP_TEXT = """📅 行事曆 - 以按鈕操作為主

【主選單】總覽、新增事件、說明（皆用按鈕）

【新增事件】依步驟按鈕選擇：日期 → 開始/結束時間 → 名稱（可選常用或選「其他」後打字）→ 地點（請直接打字，無則按略過）→ 分類 → 提醒 → 確認

【總覽】按「總覽」後選擇：全部 / 今天 / 本週 / 工作 / 個人 / 會議；每筆事件可按 詳情 / 刪除 / 修改

【修改】在總覽按「修改」後選擇要改的項目；標題與地點需打字輸入，其餘用按鈕選擇

【今日/本週】每天 7:00、每週一 7:00 自動推播行程

輸入「主選單」或按說明下方按鈕可回主選單。"""

def parse_date(s: str) -> datetime:
    s = s.strip()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "今天": return today
    if s == "明天": return today + timedelta(days=1)
    if s == "後天": return today + timedelta(days=2)
    try: return datetime.strptime(s, "%Y-%m-%d")
    except ValueError: pass
    try: return datetime.strptime(s, "%m/%d")
    except ValueError: pass
    raise ValueError(f"無法辨識的日期: {s}")

def parse_time(s: str, base_date: datetime) -> datetime:
    s = s.strip()
    if not re.match(r"^\d{1,2}:\d{2}$", s):
        raise ValueError(f"時間格式請用 HH:MM: {s}")
    h, m = map(int, s.split(":"))
    return base_date.replace(hour=h, minute=m, second=0, microsecond=0)

def format_event(e: dict) -> str:
    loc = f"\n    📍 {e['location']}" if e.get("location") else ""
    parts = e.get("participants") or ""
    part_line = f"\n    👥 邀請: {parts}" if parts else ""
    cat = f"\n    🏷 {e['category']}" if e.get("category") else ""
    return f"#{e['id']} {e['title']}\n    🕐 {e['start_time']} ~ {e['end_time']}{loc}{part_line}{cat}"

def format_event_detail(e: dict) -> str:
    """單一事件詳情長文"""
    lines = [f"📌 #{e['id']} {e['title']}", f"🕐 {e['start_time']} ~ {e['end_time']}"]
    if e.get("location"): lines.append(f"📍 {e['location']}")
    if e.get("participants"): lines.append(f"👥 邀請: {e['participants']}")
    if e.get("category"): lines.append(f"🏷 分類: {e['category']}")
    rem = e.get("reminder_minutes") or "[15]"
    try:
        mins = json.loads(rem)
        if mins: lines.append(f"⏰ 提醒: {', '.join(str(m) + '分前' for m in mins)}")
    except Exception: pass
    if e.get("recurrence_type"): lines.append(f"🔄 重複: {e['recurrence_type']} {e.get('recurrence_detail', '')}")
    return "\n".join(lines)

def get_main_quick_reply():
    return QuickReply(items=[
        QuickReplyButton(action=PostbackAction(label="📅 總覽", data="menu_overview")),
        QuickReplyButton(action=PostbackAction(label="➕ 新增事件", data="menu_add")),
        QuickReplyButton(action=PostbackAction(label="❓ 說明", data="menu_help")),
    ])

def get_main_menu_flex():
    """主選單：總覽 / 新增事件 / 說明（都用按鈕）"""
    return FlexSendMessage(
        alt_text="主選單",
        contents=BubbleContainer(
            body=BoxComponent(layout="vertical", contents=[
                TextComponent(text="📅 行事曆", weight="bold", size="xl"),
                SeparatorComponent(margin="md"),
                ButtonComponent(style="primary", action=PostbackAction(label="📅 總覽", data="menu_overview"), height="md"),
                ButtonComponent(style="primary", action=PostbackAction(label="➕ 新增事件", data="menu_add"), height="md"),
                ButtonComponent(action=PostbackAction(label="❓ 說明", data="menu_help"), height="sm"),
            ])
        )
    )

# ---------- 新增流程：按鈕步驟（僅地點需打字）----------
def _quick_dates():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        ("今天", today.strftime("%Y-%m-%d")),
        ("明天", (today + timedelta(days=1)).strftime("%Y-%m-%d")),
        ("後天", (today + timedelta(days=2)).strftime("%Y-%m-%d")),
    ] + [(f"下週{['一','二','三','四','五','六','日'][i]}", (today + timedelta(days=7 - today.weekday() + i)).strftime("%Y-%m-%d")) for i in range(7)]

def _time_options():
    return [f"{h:02d}:00" for h in range(8, 21)]

def send_step_date(reply_token):
    items = [QuickReplyButton(action=PostbackAction(label=label, data=f"add_date_{val}")) for label, val in _quick_dates()]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="📅 請選擇日期", quick_reply=QuickReply(items=items)))

def send_step_start(reply_token, date_str):
    items = [QuickReplyButton(action=PostbackAction(label=t, data=f"add_start_{t}")) for t in _time_options()]
    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🕐 請選擇開始時間（日期 {date_str}）", quick_reply=QuickReply(items=items)))

def send_step_end(reply_token, date_str, start_str):
    start_h = int(start_str.split(":")[0])
    times = [f"{h:02d}:00" for h in range(start_h + 1, 21)]
    if not times:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="結束時間至少要比開始晚 1 小時，請重新選開始時間。", quick_reply=get_main_quick_reply()))
        return
    items = [QuickReplyButton(action=PostbackAction(label=t, data=f"add_end_{t}")) for t in times[:12]]
    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🕐 請選擇結束時間（{start_str} 之後）", quick_reply=QuickReply(items=items)))

def send_step_title(reply_token):
    titles = ["會議", "晨會", "約會", "運動", "聚餐", "其他"]
    items = [QuickReplyButton(action=PostbackAction(label=t, data=f"add_title_{t}")) for t in titles]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="📌 請選擇事件名稱（或選「其他」後打字輸入）", quick_reply=QuickReply(items=items)))

def send_step_location(reply_token):
    line_bot_api.reply_message(reply_token, TextSendMessage(
        text="📍 請輸入地點（必填請直接打字），若無地點請按「略過」",
        quick_reply=QuickReply(items=[QuickReplyButton(action=PostbackAction(label="略過", data="add_location_skip"))])
    ))

def send_step_category(reply_token):
    items = [
        QuickReplyButton(action=PostbackAction(label="工作", data="add_category_工作")),
        QuickReplyButton(action=PostbackAction(label="個人", data="add_category_個人")),
        QuickReplyButton(action=PostbackAction(label="會議", data="add_category_會議")),
        QuickReplyButton(action=PostbackAction(label="其他", data="add_category_其他")),
        QuickReplyButton(action=PostbackAction(label="略過", data="add_category_略過")),
    ]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="🏷 請選擇分類", quick_reply=QuickReply(items=items)))

def send_step_reminder(reply_token):
    items = [
        QuickReplyButton(action=PostbackAction(label="不提醒", data="add_reminder_none")),
        QuickReplyButton(action=PostbackAction(label="15 分鐘前", data="add_reminder_15")),
        QuickReplyButton(action=PostbackAction(label="1 小時前", data="add_reminder_60")),
        QuickReplyButton(action=PostbackAction(label="1 天前", data="add_reminder_1440")),
    ]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="⏰ 請選擇提醒時間", quick_reply=QuickReply(items=items)))

def send_step_confirm(user_id, reply_token, data):
    loc = data.get("location") or "（無）"
    cat = data.get("category") or "（無）"
    rem = data.get("reminder_text") or "（無）"
    text = f"""✅ 請確認新增內容：
📅 日期：{data['date']}
🕐 時間：{data['start_time']} ~ {data['end_time']}
📌 名稱：{data['title']}
📍 地點：{loc}
🏷 分類：{cat}
⏰ 提醒：{rem}
"""
    user_states[user_id] = {"state": "add_confirm", "data": data}
    line_bot_api.reply_message(reply_token, TextSendMessage(
        text=text,
        quick_reply=QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="確認新增", data="add_confirm_yes")),
            QuickReplyButton(action=PostbackAction(label="取消", data="add_confirm_no")),
        ])
    ))

# ---------- 總覽篩選按鈕 ----------
def send_overview_filter(reply_token):
    items = [
        QuickReplyButton(action=PostbackAction(label="全部", data="overview_all")),
        QuickReplyButton(action=PostbackAction(label="今天", data="overview_today")),
        QuickReplyButton(action=PostbackAction(label="本週", data="overview_week")),
        QuickReplyButton(action=PostbackAction(label="工作", data="overview_工作")),
        QuickReplyButton(action=PostbackAction(label="個人", data="overview_個人")),
        QuickReplyButton(action=PostbackAction(label="會議", data="overview_會議")),
    ]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="📅 請選擇要查看的範圍", quick_reply=QuickReply(items=items)))

# ---------- 修改流程：選擇欄位後，地點/標題打字，其餘按鈕 ----------
def send_edit_choice(reply_token, eid):
    items = [
        QuickReplyButton(action=PostbackAction(label="標題", data=f"edit_{eid}_title")),
        QuickReplyButton(action=PostbackAction(label="時間", data=f"edit_{eid}_time")),
        QuickReplyButton(action=PostbackAction(label="地點", data=f"edit_{eid}_location")),
        QuickReplyButton(action=PostbackAction(label="分類", data=f"edit_{eid}_category")),
        QuickReplyButton(action=PostbackAction(label="提醒", data=f"edit_{eid}_reminder")),
    ]
    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"請選擇要修改的項目（事件 #{eid}）", quick_reply=QuickReply(items=items)))

def send_edit_location_prompt(reply_token, eid):
    line_bot_api.reply_message(reply_token, TextSendMessage(text="📍 請直接輸入新地點", quick_reply=get_main_quick_reply()))

def send_edit_title_prompt(reply_token, eid):
    line_bot_api.reply_message(reply_token, TextSendMessage(text="📌 請直接輸入新標題", quick_reply=get_main_quick_reply()))

def send_edit_time_start(reply_token, eid):
    items = [QuickReplyButton(action=PostbackAction(label=t, data=f"edit_{eid}_time_start_{t}")) for t in _time_options()]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="🕐 請選擇新的開始時間", quick_reply=QuickReply(items=items)))

def send_edit_category(reply_token, eid):
    items = [
        QuickReplyButton(action=PostbackAction(label="工作", data=f"edit_{eid}_cat_工作")),
        QuickReplyButton(action=PostbackAction(label="個人", data=f"edit_{eid}_cat_個人")),
        QuickReplyButton(action=PostbackAction(label="會議", data=f"edit_{eid}_cat_會議")),
        QuickReplyButton(action=PostbackAction(label="其他", data=f"edit_{eid}_cat_其他")),
    ]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="🏷 請選擇新分類", quick_reply=QuickReply(items=items)))

def send_edit_reminder(reply_token, eid):
    items = [
        QuickReplyButton(action=PostbackAction(label="不提醒", data=f"edit_{eid}_rem_none")),
        QuickReplyButton(action=PostbackAction(label="15分前", data=f"edit_{eid}_rem_15")),
        QuickReplyButton(action=PostbackAction(label="1小時前", data=f"edit_{eid}_rem_60")),
        QuickReplyButton(action=PostbackAction(label="1天前", data=f"edit_{eid}_rem_1440")),
    ]
    line_bot_api.reply_message(reply_token, TextSendMessage(text="⏰ 請選擇新提醒", quick_reply=QuickReply(items=items)))

# ---------- 新增（含提醒、分類、重複、衝突）----------
def try_add_event(user_id: str, text: str):
    """回傳 (reply_text, need_confirm=False) 或 (reply_text, True) 要等 Postback 確認。"""
    parts = text.split(maxsplit=5)
    if len(parts) < 5:
        return "格式：新增 日期 開始時間 結束時間 事件名稱\n可加：地點:xxx 邀請:a,b 分類:工作 提醒:15分,1小時 每週一/每月15", False
    _, date_s, start_s, end_s, title = parts[0], parts[1], parts[2], parts[3], parts[4]
    rest = parts[5] if len(parts) > 5 else ""
    location = participants = category = reminder_str = ""
    recurrence_type = recurrence_detail = ""
    for seg in rest.split():
        if seg.startswith("地點:"): location = seg[3:].strip()
        elif seg.startswith("邀請:"): participants = seg[3:].strip()
        elif seg.startswith("分類:") or seg.startswith("標籤:"): category = seg[3:].strip()
        elif seg.startswith("提醒:"): reminder_str = seg[3:].strip()
        elif seg == "每週一": recurrence_type, recurrence_detail = "weekly", "0"
        elif seg == "每週二": recurrence_type, recurrence_detail = "weekly", "1"
        elif seg == "每週三": recurrence_type, recurrence_detail = "weekly", "2"
        elif seg == "每週四": recurrence_type, recurrence_detail = "weekly", "3"
        elif seg == "每週五": recurrence_type, recurrence_detail = "weekly", "4"
        elif seg == "每週六": recurrence_type, recurrence_detail = "weekly", "5"
        elif seg == "每週日": recurrence_type, recurrence_detail = "weekly", "6"
        elif re.match(r"^每月\d+號?$", seg):
            recurrence_type = "monthly"
            recurrence_detail = re.sub(r"\D", "", seg)
    try:
        base = parse_date(date_s)
        start_dt = parse_time(start_s, base)
        end_dt = parse_time(end_s, base)
        if end_dt <= start_dt:
            return "結束時間必須晚於開始時間。", False
        start_str = start_dt.strftime("%Y-%m-%d %H:%M")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        return f"日期或時間格式錯誤：{e}", False
    reminder_list = parse_reminder(reminder_str)
    reminder_json = json.dumps(reminder_list)

    overlaps = database.get_events_overlap(user_id, start_str, end_str)
    if overlaps:
        conflict_ids = ", ".join(f"#{o['id']}" for o in overlaps[:5])
        pending_add[user_id] = {
            "title": title, "start_time": start_str, "end_time": end_str,
            "location": location, "participants": participants, "category": category,
            "reminder_minutes": reminder_json, "recurrence_type": recurrence_type, "recurrence_detail": recurrence_detail,
        }
        return f"⚠️ 與 {conflict_ids} 時間重疊，仍要新增嗎？", True

    # 無衝突或重複：直接新增（含重複事件展開）
    return _do_add_events(user_id, title, start_str, end_str, location, participants,
                          category, reminder_json, recurrence_type, recurrence_detail), False

def _do_add_events(user_id: str, title: str, start_str: str, end_str: str, location: str, participants: str,
                   category: str, reminder_json: str, recurrence_type: str, recurrence_detail: str) -> str:
    """實際寫入一筆或多筆（重複時展開）。"""
    if recurrence_type == "weekly":
        base = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        delta = datetime.strptime(end_str, "%Y-%m-%d %H:%M") - base
        ids = []
        for _ in range(52):
            start_s = base.strftime("%Y-%m-%d %H:%M")
            end_s = (base + delta).strftime("%Y-%m-%d %H:%M")
            ids.append(database.add_event(user_id, title, start_s, end_s,
                         location, participants, category, reminder_json, recurrence_type, recurrence_detail))
            base += timedelta(days=7)
        return f"✅ 已新增重複事件（每週）共 {len(ids)} 筆，編號 #{ids[0]}～#{ids[-1]}"
    if recurrence_type == "monthly":
        base = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        day = int(recurrence_detail) if recurrence_detail else base.day
        delta = datetime.strptime(end_str, "%Y-%m-%d %H:%M") - datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        ids = []
        for i in range(12):
            y = base.year + (base.month + i - 1) // 12
            m = (base.month + i - 1) % 12 + 1
            try:
                start_d = base.replace(year=y, month=m, day=min(day, 28))
            except ValueError:
                start_d = base.replace(year=y, month=m, day=1) + timedelta(days=min(day, 28) - 1)
            start_s = start_d.strftime("%Y-%m-%d %H:%M")
            end_d = start_d + delta
            ids.append(database.add_event(user_id, title, start_s, end_d.strftime("%Y-%m-%d %H:%M"),
                         location, participants, category, reminder_json, recurrence_type, recurrence_detail))
        return f"✅ 已新增重複事件（每月）共 {len(ids)} 筆，編號 #{ids[0]}～#{ids[-1]}"
    eid = database.add_event(user_id, title, start_str, end_str, location, participants,
                             category, reminder_json, recurrence_type, recurrence_detail)
    out = f"✅ 已新增事件 #{eid}\n{title}\n{start_str} ~ {end_str}"
    if location: out += f"\n📍 {location}"
    if participants: out += f"\n👥 邀請: {participants}"
    if category: out += f"\n🏷 {category}"
    return out

# ---------- 修改事件 ----------
def try_edit_event(user_id: str, text: str) -> str:
    """修改 3 標題 週會 | 修改 3 時間 15:00 16:00 | 修改 3 地點 新會議室 | 修改 3 提醒 1小時,1天"""
    parts = text.split(maxsplit=3)
    if len(parts) < 4 or not parts[1].isdigit():
        return "格式：修改 編號 欄位 新值\n欄位：標題、時間、地點、邀請、分類、提醒"
    eid = int(parts[1])
    field = parts[2].strip().lower()
    value = parts[3].strip()
    ev = database.get_event_by_id(eid, user_id)
    if not ev:
        return f"找不到事件 #{eid}。"
    updates = {}
    if field == "標題":
        updates["title"] = value
    elif field == "時間":
        try:
            start_s, end_s = value.split()
            base = datetime.strptime(ev["start_time"], "%Y-%m-%d %H:%M").replace(hour=0, minute=0, second=0)
            start_dt = parse_time(start_s, base)
            end_dt = parse_time(end_s, base)
            if end_dt <= start_dt:
                return "結束時間必須晚於開始時間。"
            updates["start_time"] = start_dt.strftime("%Y-%m-%d %H:%M")
            updates["end_time"] = end_dt.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            return f"時間格式錯誤，請用 開始 結束（例：15:00 16:00）：{e}"
    elif field == "地點":
        updates["location"] = value
    elif field == "邀請":
        updates["participants"] = value
    elif field == "分類" or field == "標籤":
        updates["category"] = value
    elif field == "提醒":
        updates["reminder_minutes"] = json.dumps(parse_reminder(value))
    else:
        return "欄位請用：標題、時間、地點、邀請、分類、提醒"
    database.update_event(eid, user_id, **updates)
    return f"✅ 已更新事件 #{eid} 的{field}。"

# ---------- 總覽（含分類、Flex 按鈕）----------
def do_overview(user_id: str, text: str):
    """回傳 (text_or_flex, use_flex=False)。若事件數>0 且無指定日期區間則回傳 Flex。"""
    parts = text.split()
    from_date = to_date = category = None
    if len(parts) >= 2:
        if parts[1] in ("工作", "個人", "會議", "其他"):
            category = parts[1]
        else:
            try:
                d = parse_date(parts[1])
                from_date = d.strftime("%Y-%m-%d 00:00")
                to_date = (d + timedelta(days=1)).strftime("%Y-%m-%d 23:59")
            except ValueError: pass
    if len(parts) >= 3 and not category:
        try:
            d2 = parse_date(parts[2])
            to_date = (d2 + timedelta(days=1)).strftime("%Y-%m-%d 23:59")
        except ValueError: pass
    events = database.get_events_by_user(user_id, from_date, to_date, category)
    if not events:
        hint = f"（{category or (from_date or '全部')}）"
        return f"📅 目前沒有排程事件{hint}。\n輸入「新增」可新增事件。", False
    # 總覽按鈕：用 Flex Carousel，每筆一個 bubble，內有 詳情 / 刪除 / 修改 按鈕
    bubbles = []
    for e in events[:10]:
        body_lines = [f"#{e['id']} {e['title']}", f"🕐 {e['start_time']} ~ {e['end_time']}"]
        if e.get("location"): body_lines.append(f"📍 {e['location']}")
        if e.get("category"): body_lines.append(f"🏷 {e['category']}")
        contents = [TextComponent(text=line, wrap=True) for line in body_lines]
        contents.append(SeparatorComponent(margin="md"))
        contents.append(BoxComponent(layout="horizontal", spacing="sm", contents=[
            ButtonComponent(style="primary", height="sm", action=PostbackAction(label="詳情", data=f"detail_{e['id']}")),
            ButtonComponent(style="link", height="sm", action=PostbackAction(label="刪除", data=f"delete_{e['id']}")),
            ButtonComponent(style="link", height="sm", action=PostbackAction(label="修改", data=f"edit_{e['id']}")),
        ]))
        bubbles.append(BubbleContainer(body=BoxComponent(layout="vertical", contents=contents)))
    carousel = CarouselContainer(contents=bubbles)
    return FlexSendMessage(alt_text="📅 行事曆總覽", contents=carousel), True

# ---------- 單一事件詳情 ----------
def do_event_detail(user_id: str, eid: int) -> str:
    ev = database.get_event_by_id(eid, user_id)
    if not ev:
        return f"找不到事件 #{eid}。"
    return format_event_detail(ev)

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
    reply_token = event.reply_token

    # ---------- 狀態：等待使用者「打字」的步驟（僅地點、自訂標題、修改地點/標題）----------
    state = user_states.get(user_id)
    if state:
        s = state.get("state")
        if s == "add_location":
            user_states.pop(user_id, None)
            data = state.get("data", {})
            data["location"] = text
            user_states[user_id] = {"state": "add_category", "data": data}
            send_step_category(reply_token)
            return
        if s == "add_title_other":
            user_states.pop(user_id, None)
            data = state.get("data", {})
            data["title"] = text
            user_states[user_id] = {"state": "add_location", "data": data}
            send_step_location(reply_token)
            return
        if s == "edit_location":
            eid = state.get("eid")
            user_states.pop(user_id, None)
            if eid and database.update_event(eid, user_id, location=text):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ 已更新事件 #{eid} 的地點。", quick_reply=get_main_quick_reply()))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="更新失敗。", quick_reply=get_main_quick_reply()))
            return
        if s == "edit_title":
            eid = state.get("eid")
            user_states.pop(user_id, None)
            if eid and database.update_event(eid, user_id, title=text):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ 已更新事件 #{eid} 的標題。", quick_reply=get_main_quick_reply()))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="更新失敗。", quick_reply=get_main_quick_reply()))
            return

    # ---------- 無狀態或一般指令：用按鈕主選單，不再用打字指令 ----------
    if not text or text in ("主選單", "選單", "menu"):
        line_bot_api.reply_message(reply_token, get_main_menu_flex())
        return
    if text in ("說明", "幫助", "help"):
        line_bot_api.reply_message(reply_token, TextSendMessage(text=HELP_TEXT, quick_reply=get_main_quick_reply()))
        return
    if text in ("總覽", "行事曆"):
        send_overview_filter(reply_token)
        return
    if text == "新增":
        user_states[user_id] = {"state": "add_date", "data": {}}
        send_step_date(reply_token)
        return
    # 其餘一律導回主選單（引導用按鈕）
    line_bot_api.reply_message(reply_token, TextSendMessage(
        text="請用下方按鈕操作：總覽、新增事件、說明。若要輸入地點或自訂名稱，請在流程中依提示打字。",
        quick_reply=get_main_quick_reply()
    ))

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = (event.postback.data or "").strip()
    reply_token = event.reply_token

    # ---------- 主選單 ----------
    if data == "menu_overview":
        send_overview_filter(reply_token)
        return
    if data == "menu_add":
        user_states[user_id] = {"state": "add_date", "data": {}}
        send_step_date(reply_token)
        return
    if data == "menu_help":
        line_bot_api.reply_message(reply_token, TextSendMessage(text=HELP_TEXT, quick_reply=get_main_quick_reply()))
        return

    # ---------- 總覽篩選 ----------
    if data == "overview_all":
        ov, use_flex = do_overview(user_id, "總覽")
        msg = ov if use_flex else TextSendMessage(text=ov, quick_reply=get_main_quick_reply())
        line_bot_api.reply_message(reply_token, msg)
        return
    if data == "overview_today":
        today = datetime.now().strftime("%Y-%m-%d")
        ov, use_flex = do_overview(user_id, f"總覽 {today}")
        msg = ov if use_flex else TextSendMessage(text=ov, quick_reply=get_main_quick_reply())
        line_bot_api.reply_message(reply_token, msg)
        return
    if data == "overview_week":
        now = datetime.now()
        mon = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        sun = (now + timedelta(days=6 - now.weekday())).strftime("%Y-%m-%d")
        ov, use_flex = do_overview(user_id, f"總覽 {mon} {sun}")
        msg = ov if use_flex else TextSendMessage(text=ov, quick_reply=get_main_quick_reply())
        line_bot_api.reply_message(reply_token, msg)
        return
    if data.startswith("overview_"):
        cat = data.replace("overview_", "")
        ov, use_flex = do_overview(user_id, f"總覽 {cat}")
        msg = ov if use_flex else TextSendMessage(text=ov, quick_reply=get_main_quick_reply())
        line_bot_api.reply_message(reply_token, msg)
        return

    # ---------- 新增流程（按鈕步驟）----------
    if data.startswith("add_date_"):
        date_val = data.replace("add_date_", "")
        user_states[user_id] = {"state": "add_start", "data": {"date": date_val}}
        send_step_start(reply_token, date_val)
        return
    if data.startswith("add_start_"):
        start_t = data.replace("add_start_", "")
        state = user_states.get(user_id)
        if state and state.get("state") == "add_start":
            d = state["data"].copy()
            d["start_time"] = start_t
            user_states[user_id] = {"state": "add_end", "data": d}
            send_step_end(reply_token, d["date"], start_t)
        return
    if data.startswith("add_end_"):
        end_t = data.replace("add_end_", "")
        state = user_states.get(user_id)
        if state and state.get("state") == "add_end":
            d = state["data"].copy()
            d["end_time"] = end_t
            user_states[user_id] = {"state": "add_title", "data": d}
            send_step_title(reply_token)
        return
    if data.startswith("add_title_"):
        title_val = data.replace("add_title_", "")
        state = user_states.get(user_id)
        if not state: return
        d = state["data"].copy()
        if title_val == "其他":
            user_states[user_id] = {"state": "add_title_other", "data": d}
            line_bot_api.reply_message(reply_token, TextSendMessage(text="📌 請直接輸入事件名稱", quick_reply=get_main_quick_reply()))
        else:
            d["title"] = title_val
            user_states[user_id] = {"state": "add_location", "data": d}
            send_step_location(reply_token)
        return
    if data == "add_location_skip":
        state = user_states.get(user_id)
        if state and state.get("state") == "add_location":
            d = state["data"].copy()
            d["location"] = ""
            user_states[user_id] = {"state": "add_category", "data": d}
            send_step_category(reply_token)
        return
    if data.startswith("add_category_"):
        cat = data.replace("add_category_", "")
        state = user_states.get(user_id)
        if state:
            d = state["data"].copy()
            d["category"] = "" if cat == "略過" else cat
            user_states[user_id] = {"state": "add_reminder", "data": d}
            send_step_reminder(reply_token)
        return
    if data.startswith("add_reminder_"):
        rem = data.replace("add_reminder_", "")
        state = user_states.get(user_id)
        if state:
            d = state["data"].copy()
            if rem == "none":
                d["reminder_minutes"] = "[]"
                d["reminder_text"] = "不提醒"
            else:
                m = int(rem)
                d["reminder_minutes"] = json.dumps([m])
                d["reminder_text"] = {15: "15分前", 60: "1小時前", 1440: "1天前"}.get(m, f"{m}分前")
            user_states[user_id] = {"state": "add_confirm", "data": d}
            send_step_confirm(user_id, reply_token, d)
        return
    if data == "add_confirm_yes":
        state = user_states.pop(user_id, None)
        if state and state.get("state") == "add_confirm":
            d = state["data"]
            start_str = f"{d['date']} {d['start_time']}"
            end_str = f"{d['date']} {d['end_time']}"
            rem = d.get("reminder_minutes", "[15]")
            overlaps = database.get_events_overlap(user_id, start_str, end_str)
            if overlaps:
                conflict_ids = ", ".join(f"#{o['id']}" for o in overlaps[:3])
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"⚠️ 與 {conflict_ids} 時間重疊，已取消新增。請從主選單重新新增並改選時間。",
                    quick_reply=get_main_quick_reply()
                ))
                return
            msg = _do_add_events(user_id, d["title"], start_str, end_str, d.get("location", ""), "",
                                 d.get("category", ""), rem, "", "")
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg, quick_reply=get_main_quick_reply()))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="已逾時，請從主選單重新新增。", quick_reply=get_main_quick_reply()))
        return
    if data == "add_confirm_no":
        user_states.pop(user_id, None)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="已取消新增。", quick_reply=get_main_quick_reply()))
        return

    # ---------- 衝突確認（舊版打字新增的流程保留）----------
    if data == "confirm_add_yes":
        if user_id in pending_add:
            p = pending_add.pop(user_id)
            msg = _do_add_events(user_id, p["title"], p["start_time"], p["end_time"],
                                 p["location"], p["participants"], p["category"],
                                 p["reminder_minutes"], p["recurrence_type"], p["recurrence_detail"])
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg, quick_reply=get_main_quick_reply()))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="已逾時，請重新新增。", quick_reply=get_main_quick_reply()))
        return
    if data == "confirm_add_no":
        pending_add.pop(user_id, None)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="已取消新增。", quick_reply=get_main_quick_reply()))
        return

    # ---------- 總覽內：詳情 / 刪除 / 修改 ----------
    if data.startswith("detail_"):
        eid = int(data.split("_")[1])
        line_bot_api.reply_message(reply_token, TextSendMessage(text=do_event_detail(user_id, eid), quick_reply=get_main_quick_reply()))
        return
    if data.startswith("delete_"):
        eid = int(data.split("_")[1])
        if database.delete_event(eid, user_id):
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ 已刪除事件 #{eid}", quick_reply=get_main_quick_reply()))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"無法刪除事件 #{eid}", quick_reply=get_main_quick_reply()))
        return
    if data.startswith("edit_") and data.count("_") == 2:
        # edit_3_title, edit_3_location, edit_3_time, edit_3_category, edit_3_reminder
        parts = data.split("_")
        eid = int(parts[1])
        field = parts[2]
        if field == "title":
            user_states[user_id] = {"state": "edit_title", "eid": eid}
            send_edit_title_prompt(reply_token, eid)
        elif field == "location":
            user_states[user_id] = {"state": "edit_location", "eid": eid}
            send_edit_location_prompt(reply_token, eid)
        elif field == "time":
            user_states[user_id] = {"state": "edit_time_start", "eid": eid, "data": {}}
            send_edit_time_start(reply_token, eid)
        elif field == "category":
            send_edit_category(reply_token, eid)
        elif field == "reminder":
            send_edit_reminder(reply_token, eid)
        return
    if data.startswith("edit_") and "_time_start_" in data:
        # edit_3_time_start_09:00
        parts = data.split("_")
        eid = int(parts[1])
        start_t = parts[-1]
        user_states[user_id] = {"state": "edit_time_end", "eid": eid, "data": {"start": start_t}}
        times = [f"{h:02d}:00" for h in range(int(start_t.split(":")[0]) + 1, 21)]
        items = [QuickReplyButton(action=PostbackAction(label=t, data=f"edit_{eid}_time_end_{t}")) for t in times[:12]]
        line_bot_api.reply_message(reply_token, TextSendMessage(text="🕐 請選擇結束時間", quick_reply=QuickReply(items=items)))
        return
    if data.startswith("edit_") and "_time_end_" in data:
        parts = data.split("_")
        eid = int(parts[1])
        end_t = parts[-1]
        state = user_states.get(user_id)
        if state and state.get("state") == "edit_time_end" and state.get("eid") == eid:
            start_t = state.get("data", {}).get("start")
            ev = database.get_event_by_id(eid, user_id)
            if ev and start_t:
                base = datetime.strptime(ev["start_time"], "%Y-%m-%d %H:%M").replace(hour=0, minute=0)
                start_dt = parse_time(start_t, base)
                end_dt = parse_time(end_t, base)
                if end_dt > start_dt:
                    database.update_event(eid, user_id, start_time=start_dt.strftime("%Y-%m-%d %H:%M"), end_time=end_dt.strftime("%Y-%m-%d %H:%M"))
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ 已更新事件 #{eid} 的時間。", quick_reply=get_main_quick_reply()))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="結束時間須晚於開始時間。", quick_reply=get_main_quick_reply()))
            user_states.pop(user_id, None)
        return
    if data.startswith("edit_") and "_cat_" in data:
        # edit_3_cat_工作
        parts = data.split("_")
        eid = int(parts[1])
        cat = parts[-1]
        database.update_event(eid, user_id, category=cat)
        user_states.pop(user_id, None)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ 已更新事件 #{eid} 的分類。", quick_reply=get_main_quick_reply()))
        return
    if data.startswith("edit_") and "_rem_" in data:
        # edit_3_rem_15, edit_3_rem_none
        parts = data.split("_")
        eid = int(parts[1])
        rem = parts[-1]
        if rem == "none":
            database.update_event(eid, user_id, reminder_minutes="[]")
        else:
            database.update_event(eid, user_id, reminder_minutes=json.dumps([int(rem)]))
        user_states.pop(user_id, None)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ 已更新事件 #{eid} 的提醒。", quick_reply=get_main_quick_reply()))
        return

# ---------- 排程：多筆提醒 + 今日/本週摘要 ----------
def send_reminders():
    from linebot.models import TextSendMessage
    now = datetime.now()
    for user_id in database.get_all_user_ids():
        events = database.get_events_by_user(user_id)
        for e in events:
            try:
                rem_list = json.loads(e.get("reminder_minutes") or "[15]")
            except Exception:
                rem_list = [15]
            start_dt = datetime.strptime(e["start_time"], "%Y-%m-%d %H:%M")
            for offset_min in rem_list:
                reminder_at = start_dt - timedelta(minutes=offset_min)
                if now >= reminder_at - timedelta(minutes=5) and now <= reminder_at + timedelta(minutes=5):
                    if database.reminder_was_sent(e["id"], offset_min):
                        continue
                    msg = f"⏰ 提醒（{offset_min} 分鐘前）：{e['title']}\n時間：{e['start_time']} ~ {e['end_time']}"
                    if e.get("location"): msg += f"\n📍 {e['location']}"
                    if e.get("participants"): msg += f"\n👥 {e['participants']}"
                    try:
                        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
                        database.mark_reminder_sent(e["id"], offset_min)
                    except Exception: pass

def send_daily_digest():
    from linebot.models import TextSendMessage
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = (today_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    today_start_s = today_start.strftime("%Y-%m-%d %H:%M")
    for user_id in database.get_all_user_ids():
        events = database.get_events_by_user(user_id, today_start_s, today_end)
        if not events: continue
        lines = ["📅 今日行程"]
        for e in events:
            lines.append(format_event(e))
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text="\n".join(lines)))
        except Exception: pass

def send_weekly_digest():
    from linebot.models import TextSendMessage
    now = datetime.now()
    # 本週一 00:00 到下週一 00:00
    weekday = now.weekday()
    monday = now - timedelta(days=weekday)
    week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    start_s = week_start.strftime("%Y-%m-%d %H:%M")
    end_s = week_end.strftime("%Y-%m-%d %H:%M")
    for user_id in database.get_all_user_ids():
        events = database.get_events_by_user(user_id, start_s, end_s)
        if not events: continue
        lines = ["📅 本週行程"]
        for e in events:
            lines.append(format_event(e))
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text="\n".join(lines)))
        except Exception: pass

def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_reminders, "interval", minutes=5)
    scheduler.add_job(send_daily_digest, "cron", hour=7, minute=0)
    scheduler.add_job(send_weekly_digest, "cron", day_of_week="mon", hour=7, minute=0)
    scheduler.start()

_init_done = False
@app.before_request
def _ensure_init():
    global _init_done
    if not _init_done:
        database.init_db()
        start_scheduler()
        _init_done = True

if __name__ == "__main__":
    # 在 Render 上這塊不會執行，所以不用擔心
    app.run()

# 確保這兩行在 app.run 之外且不會卡死
try:
    database.init_db()
    start_scheduler()
except Exception as e:
    print(f"Init error: {e}")
