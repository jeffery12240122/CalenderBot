# LINE 行事曆機器人

用 Python 寫的 LINE 機器人，當作個人行事曆使用：可**新增事件**、**總覽**查看、**時間 / 地點 / 邀請對象**，並在事件開始前**自動提醒**。

## 功能

- **新增事件**：日期、開始/結束時間、標題，可選地點與邀請對象
- **總覽**：查看全部或指定日期區間的事件
- **刪除事件**：依事件編號刪除
- **提醒**：每 5 分鐘檢查一次，事件開始前約 15 分鐘內會推播提醒（含時間、地點、邀請對象）

## 環境需求

- Python 3.8+
- LINE 官方帳號（Messaging API）

## 安裝

```bash
cd c:\Users\jefferyl\CURSORPJ
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## LINE Developers 設定

1. 前往 [LINE Developers](https://developers.line.biz/) 建立 Provider 與 Channel（Messaging API）。
2. 在 Channel 取得：
   - **Channel secret**
   - **Channel access token**（長期）
3. 設定 **Webhook URL**：  
   本機測試可用 ngrok：`ngrok http 5000`，然後把 `https://xxxx.ngrok.io/callback` 設為 Webhook URL。
4. 關閉「Use webhook」下方的「Auto-reply messages」與「Greeting messages」，避免與機器人衝突。

## 執行前設定

用環境變數或改 `config.py` 設定：

- `LINE_CHANNEL_ACCESS_TOKEN`：Channel access token
- `LINE_CHANNEL_SECRET`：Channel secret

Windows PowerShell 範例：

```powershell
$env:LINE_CHANNEL_ACCESS_TOKEN="你的_access_token"
$env:LINE_CHANNEL_SECRET="你的_channel_secret"
python app.py
```

或建立 `.env` 檔（勿提交到版控），用 `python-dotenv` 載入亦可。

## 使用方式

在 LINE 對機器人輸入：

| 指令 | 說明 |
|------|------|
| `說明` | 顯示所有指令說明 |
| `總覽` 或 `行事曆` | 顯示所有事件 |
| `總覽 2025-03-15` | 顯示該日事件 |
| `總覽 2025-03-15 2025-03-20` | 顯示該區間事件 |
| `新增 日期 開始時間 結束時間 事件名稱` | 新增事件（可加 `地點:xxx`、`邀請:a,b`） |
| `刪除 事件編號` | 刪除該事件 |

### 新增事件範例

```
新增 2025-03-15 14:00 16:00 團隊會議 地點:會議室A 邀請:小明,小華
新增 明天 09:00 10:00 晨會 地點:總部
```

日期可輸入：`今天`、`明天`、`後天` 或 `YYYY-MM-DD`。  
時間格式：`HH:MM`（例如 14:00、9:00）。

## 專案結構

```
CURSORPJ/
├── app.py          # Flask + LINE Webhook + 排程提醒
├── config.py       # 設定（token、secret、DB 路徑）
├── database.py     # SQLite 事件存取
├── Procfile        # 本機/Heroku 用；Render 以 render.yaml 為準
├── render.yaml     # Render Blueprint（部署設定在 GitHub）
├── requirements.txt
└── README.md
```

執行後會在同目錄產生 `calendar.db`（SQLite）存放事件。

## 部署到 Render（用 Blueprint）

專案裡有 **render.yaml**（Render Blueprint），部署設定都在 GitHub 上，Render 會依此建立服務。

1. **程式推上 GitHub**  
   請勿把 token、secret 寫進 `config.py`；Render 用環境變數即可。

2. **在 Render 用 Blueprint 建立**  
   - 登入 [Render](https://render.com) → **New** → **Blueprint**  
   - 連到 GitHub，選 **jeffery12240122/CalenderBot**（或你的 repo）  
   - Render 會讀取根目錄的 `render.yaml`，建立 Web Service  
   - 建立時會提示輸入 `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_CHANNEL_SECRET`，請貼上 LINE Developers 的 token 與 secret  

3. **若已手動建過 Web Service，想改為 Blueprint**  
   - 在該服務的 **Settings** 可改為從 Blueprint 同步；或刪除舊服務，改選 **New → Blueprint** 再連同一個 repo  

4. **部署完成後**  
   - 在 Render 會得到一個網址，例如 `https://calenderbot.onrender.com`  
   - 到 [LINE Developers](https://developers.line.biz/console/) → 你的 Channel → Messaging API  
   - 把 **Webhook URL** 改成：`https://你的服務名稱.onrender.com/callback`  
   - 按 **Verify** 確認成功  

之後每次 push 到 GitHub，Render 會依 Blueprint 自動重新 build 並部署。

> **注意**：Render 免費方案重啟或重新部署時，磁碟會還原，SQLite 的 `calendar.db` 不會保留。若需要長期保存事件，可之後改接 Render PostgreSQL 或外部資料庫。

**若出現「Address already in use」或 port 9999 錯誤**：到 Render Dashboard → 你的服務 → **Settings** → **Start Command**。請設成空白（使用 Blueprint 的設定）或填：`gunicorn -w 1 -b 0.0.0.0:$PORT app:app`，不要用 `/bin/bash` 或其他指令。儲存後再 **Manual Deploy** 一次。

---

完成以上步驟後，即可在 LINE 用「總覽」「新增」「刪除」管理行事曆，並在事件前收到提醒。
