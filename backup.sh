#!/bin/bash
# backup.sh — 本地 cron 觸發，本地 git 指令 push 到 GitHub 私有倉庫。
# GitHub 不執行任何運算，只作為異地儲存目的地，符合「最低算力本地運行」原則。
#
# 前置需求（僅需設定一次）：
#   1. 已在此目錄執行過 `git init` 且已設定 remote origin（私有倉庫）
#   2. 已設定 fine-grained PAT 或 SSH deploy key，且僅有該倉庫的 push 權限
#
# 建議排程：crontab 每日凌晨執行一次，見 crontab.example
#
# 兩份 log 用途明確分離（依 git push 的 exit code 判斷，不是看有沒有輸出內容——
# git push 成功時也會把 ref 更新資訊寫到 stderr，用「有沒有輸出」判斷會誤把
# 正常訊息當成錯誤）：
#   logs/backup.log        每次執行都會有一行摘要（成功或失敗都記）
#   logs/backup_errors.log 只有真正失敗時才會有內容；空檔案 = 從未失敗過

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
    echo "[$(ts)] 備份失敗（加密步驟），詳見 logs/backup_errors.log"
    exit 1
fi

# 2. 只加入不含明碼個資的檔案；.env、data.db、原始 .md/.json 明碼版本
#    透過 .gitignore 排除，此處不重複列出以免兩處清單不同步。
#    intel-pusher 目前沒有 PII_FILES（見 encrypt_backup.py），所以沒有
#    對應的 .enc 檔案要加；等這個專案真的產生個資檔案時再加進來。
git add scrapers/ main.py db.py push_webhook.py encrypt_backup.py schema.sql \
        summarizer_zh.py summarizer_en.py \
        README.md requirements.txt crontab.example backup.sh \
        .gitignore .env.example Meta_Dev_Knowledge.md Meta_User_Feedback.md 2>/dev/null || true

# 3. 若沒有變更，git commit 會因為「nothing to commit」而失敗（不是產生空
#    commit），用 || true 避免中斷腳本；--allow-empty-message 只是允許空白
#    的 commit「訊息」，跟允許空白「變更」是兩回事，這裡刻意不加
#    --allow-empty，所以沒有變更的日子不會產生無意義的空 commit。
git commit -m "auto backup $(ts)" --allow-empty-message >/dev/null 2>&1 || true

# 4. push；用 exit code 判斷成功/失敗，push 的完整輸出先存起來，
#    只有失敗時才寫進 backup_errors.log。刻意不用 set -e，這裡才是
#    真正需要「失敗也要繼續往下走、把錯誤記下來」的地方。
push_output="$(git push origin main 2>&1)"
push_exit=$?
commit_hash="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [ "$push_exit" -eq 0 ]; then
    echo "[$(ts)] 成功，commit=$commit_hash" >> "$BACKUP_LOG"
    echo "[$(ts)] 備份完成並已推送（commit=$commit_hash）"
else
    {
        echo "[$(ts)] 失敗，commit=$commit_hash"
        echo "$push_output"
    } >> "$ERROR_LOG"
    echo "[$(ts)] 備份 push 失敗（commit=$commit_hash），詳見 logs/backup_errors.log"
fi
