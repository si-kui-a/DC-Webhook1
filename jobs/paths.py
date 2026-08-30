"""Extracted from main.py by _extract_jobs.py (2026-08-30) — see git history for the original single-file version. Logic below is a verbatim move, not a rewrite."""
import os
from datetime import timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.join(PROJECT_ROOT, "work")
SOURCE_HEALTH_PATH = os.path.join(WORK_DIR, "source_health.json")
os.makedirs(WORK_DIR, exist_ok=True)

# 晚間彙整/投資組合/加密貨幣回顧三個domain都要用同一個台灣時區常數
TAIWAN_TZ = timezone(timedelta(hours=8))
