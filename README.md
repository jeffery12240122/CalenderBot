# LINE 行事曆機器人

用 Python 寫的 LINE 機器人，當作個人行事曆使用：可**新增事件**、**總攬**查看、**時間 / 地點 / 邀請對象**，並在事件開始前**自動提醒**。

## 功能

- **新增事件**：日期、開始/結束時間、標題，可選地點與邀請對象
- **總攬**：查看全部或指定日期區間的事件
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
| `總攬` 或 `行事曆` | 顯示所有事件 |
| `總攬 2025-03-15` | 顯示該日事件 |
| `總攬 2025-03-15 2025-03-20` | 顯示該區間事件 |
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
├── requirements.txt
└── README.md
```

執行後會在同目錄產生 `calendar.db`（SQLite）存放事件。

## 部署到 Render

1. **程式推上 GitHub**  
   請勿把 token、secret 寫進 `config.py` 再推上去；本機可用環境變數或本地改 config，Render 用環境變數即可。

2. **在 Render 建立 Web Service**  
   - 登入 [Render](https://render.com) → New → Web Service  
   - 連到你的 GitHub repo，選這個專案  
   - **Runtime**：Python 3  
   - **Build Command**：`pip install -r requirements.txt`（或留空，Render 會自動偵測）  
   - **Start Command**：留空即可（會用專案裡的 `Procfile`：`gunicorn -w 1 ...`）  
   - **Environment**：新增兩個變數  
     - `LINE_CHANNEL_ACCESS_TOKEN` = 你的 Channel access token  
     - `LINE_CHANNEL_SECRET` = 你的 Channel secret  

3. **部署完成後**  
   - 在 Render 會得到一個網址，例如 `https://你的服務名稱.onrender.com`  
   - 到 [LINE Developers](https://developers.line.biz/console/) → 你的 Channel → Messaging API  
   - 把 **Webhook URL** 改成：`https://你的服務名稱.onrender.com/callback`  
   - 按 **Verify** 確認成功  

之後每次 push 到 GitHub，Render 會自動重新 build 並部署。

> **注意**：Render 免費方案重啟或重新部署時，磁碟會還原，SQLite 的 `calendar.db` 不會保留。若需要長期保存事件，可之後改接 Render PostgreSQL 或外部資料庫。

---

完成以上步驟後，即可在 LINE 用「總攬」「新增」「刪除」管理行事曆，並在事件前收到提醒。
