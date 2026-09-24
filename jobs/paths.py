"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import os
from datetime import timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.join(PROJECT_ROOT, "work")
SOURCE_HEALTH_PATH = os.path.join(WORK_DIR, "source_health.json")
os.makedirs(WORK_DIR, exist_ok=True)

# data/ 存放「合併後」資料檔(跟work/純log/執行期狀態分開)，第一個用途是
# thu_calendar.py的當期學期行事曆。2026-09-25起該檔不進版控(.gitignore)：
# 沒有其他程式讀它，每天重新產生，進版控只會讓main每天留下未commit變更。
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
THU_CALENDAR_PATH = os.path.join(DATA_DIR, "thu_academic_calendar.json")
os.makedirs(DATA_DIR, exist_ok=True)

# 晚間彙整/投資組合/加密貨幣回顧三個domain都要用同一個台灣時區常數
TAIWAN_TZ = timezone(timedelta(hours=8))
