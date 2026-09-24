#!/bin/bash
# backup.sh — Windows Task Scheduler(IntelPusher-Backup)每晚觸發。
#
# 2026-09-25起只剩「加密含 PII 的檔案」一步(目前 PII_FILES 為空，等於不做事)。
# 原本的 git add/commit/push 已移除：pre-commit guard 擋下所有直接 commit 到
# main，程式碼異動一律走 feature branch + PR，夜間自動 commit 從來不會成功，
# 舊寫法又把失敗吞掉、log 照寫「成功」(見 docs/DECISIONS.md 2026-09-25)。
# .enc 檔若日後真的產生，跟其他異動一樣走 PR 進版控。
#
#   logs/backup.log        每次執行一行摘要（成功或失敗都記）
#   logs/backup_errors.log 只有真正失敗時才會有內容

# 刻意不用 set -e：底下每一步都要能在失敗時把錯誤寫進 ERROR_LOG 再結束，
# set -e 配合 var=$(cmd) 這種寫法，一旦 cmd 失敗會讓整支腳本立刻中止在那一行
# ——連進到「寫錯誤日誌」的程式碼都到不了，等於失敗被吃掉、backup_errors.log
# 永遠是空的（這是第一版分離日誌邏輯時實際踩到的 bug，靠手動重跑抓出來）。
# 改成每一步自己判斷 exit code，該記錄就記錄，不靠 set -e 幫忙中止。
cd "$(dirname "$0")"

LOG_DIR="logs"
BACKUP_LOG="$LOG_DIR/backup.log"
ERROR_LOG="$LOG_DIR/backup_errors.log"
mkdir -p "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(ts)] 開始備份" >> "$BACKUP_LOG"

# 1. 加密含 PII 的檔案（產生/更新 .enc）
# 用專案 venv 的 python，不要用系統 python3——cryptography 只裝在 venv 裡，
# 系統 python3（Windows Store App Execution Alias）沒有這個套件，
# 直接呼叫 python3 會在排程執行時失敗（已在首次手動試跑時實測到這個錯誤）。
encrypt_output="$("$(dirname "$0")/venv/Scripts/python.exe" encrypt_backup.py 2>&1)"
encrypt_exit=$?
if [ "$encrypt_exit" -ne 0 ]; then
    {
        echo "[$(ts)] 失敗，加密步驟 encrypt_backup.py 錯誤"
        echo "$encrypt_output"
    } >> "$ERROR_LOG"
    echo "[$(ts)] 失敗（加密步驟），詳見 logs/backup_errors.log" >> "$BACKUP_LOG"
    echo "[$(ts)] 備份失敗（加密步驟），詳見 logs/backup_errors.log"
    exit 1
fi

echo "[$(ts)] 成功（僅加密步驟；程式碼備份走 PR 流程）" >> "$BACKUP_LOG"
exit 0
