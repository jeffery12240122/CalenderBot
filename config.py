# LINE 機器人設定 - 請從 LINE Developers 取得後填入
import os

# 本機可寫在這裡或用環境變數；Render 上請在 Dashboard 設環境變數，勿把真實值推上 GitHub
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")

# 資料庫路徑
DATABASE_PATH = os.environ.get("DATABASE_PATH", "calendar.db")
