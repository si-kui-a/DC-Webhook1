#!/bin/bash
# 影片 -> 音軌抽取 -> 逐字稿(json)，全自動、無需AI介入這個階段。
# 2026-08-06從wedding-marketing-archive會議記錄整理任務中正式收錄，
# 取代每次重新手寫bash迴圈的做法。
#
# 用法：meeting_pipeline.sh <影片資料夾> <輸出資料夾>
# 對<影片資料夾>底下每個.mp4，各自產生 <輸出資料夾>/<檔名去副檔名>.json
# 已存在的json會跳過(方便中斷後重跑)。
#
# 之後的步驟(判斷主題、分段、挑截圖時間點、寫meeting record md)本身
# 需要語意理解，不在本腳本範圍內——這支腳本刻意只做到"能百分之百用
# 程式做完"的部分為止。

set -e
SRC_DIR="$1"
OUT_DIR="$2"
FFMPEG="C:\Users\User\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/.venv/Scripts/python.exe"

if [ -z "$SRC_DIR" ] || [ -z "$OUT_DIR" ]; then
  echo "用法: meeting_pipeline.sh <影片資料夾> <輸出資料夾>" >&2
  exit 1
fi

mkdir -p "$OUT_DIR/audio" "$OUT_DIR/segments"

find "$SRC_DIR" -iname "*.mp4" | while IFS= read -r src; do
  base="$(basename "$src" .mp4)"
  wav="$OUT_DIR/audio/$base.wav"
  seg="$OUT_DIR/segments/$base.json"
  if [ -f "$seg" ]; then
    echo "跳過(已存在): $base"
    continue
  fi
  echo "=== $base ==="
  echo "  抽音軌..."
  "$FFMPEG" -y -i "$src" -vn -ac 1 -ar 16000 "$wav" >/dev/null 2>&1
  echo "  轉錄中(GPU)..."
  "$PY" "$HERE/meeting_transcribe.py" "$wav" "$seg"
done

echo "全部完成，逐字稿在 $OUT_DIR/segments/"
