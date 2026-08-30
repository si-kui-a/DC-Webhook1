"""
video_digest.py — 資料夾影片批次轉逐字稿+摘要+PDF+推播DC(2026-08-05新增)。

流程(單支影片)：
    ffmpeg抽音軌(wav) -> transcribe_segments()(faster-whisper，帶時間戳)
    -> correct_typos()逐段清理 -> TextRank摘要(correct_and_summarize.summarize())
    -> select_highlights()(唯一的AI呼叫，挑重點時刻) -> ffmpeg對重點時刻截圖
    -> build_pdf() -> Discord建討論串(影片檔名) -> 推修正稿+摘要文字 ->
    上傳PDF

一支影片失敗不中斷整批(見_process_one的try/except)，這是資料夾批次工具的
基本要求——不能因為某支影片壞掉(損毀/無聲軌)就讓後面的片全部沒處理到。

用法：
    .venv/Scripts/python.exe video_digest.py --folder "C:/path/to/videos"
"""
import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime

from correct_and_summarize import correct_typos, summarize
from discord_push import BOT_TOKEN, CHANNEL_ID, create_thread, post_file, post_message
from pdf_report import build_pdf
from select_highlights import select_highlights
from transcribe import transcribe_segments

FFMPEG = "C:/Users/User/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
_NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def extract_audio(video_path: str, out_wav: str):
    subprocess.run(
        [FFMPEG, "-y", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", out_wav],
        check=True, capture_output=True, creationflags=_NO_WINDOW_FLAGS,
    )


def screenshot_at(video_path: str, timestamp: float, out_jpg: str):
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(timestamp), "-i", video_path, "-frames:v", "1", out_jpg],
        check=True, capture_output=True, creationflags=_NO_WINDOW_FLAGS,
    )


def _process_one(video_path: str, work_dir: str, output_dir: str, push_to_discord: bool = True):
    name = os.path.splitext(os.path.basename(video_path))[0]
    print(f"=== {name} ===")

    wav_path = os.path.join(work_dir, f"{name}.wav")
    extract_audio(video_path, wav_path)

    print("轉錄中(faster-whisper)...")
    segments = transcribe_segments(wav_path)
    if not segments:
        print(f"[跳過] {name}：轉錄結果為空(可能無聲軌/整段靜音)")
        return

    corrected_segments = [{"start": s["start"], "text": correct_typos(s["text"])} for s in segments]
    corrected_text = "".join(s["text"] for s in corrected_segments)
    summary = summarize(corrected_text)

    print("AI挑重點環節中...")
    highlights = select_highlights(segments)

    screenshot_paths = {}
    for h in highlights:
        shot_path = os.path.join(work_dir, f"{name}_{h['timestamp']:.0f}s.jpg")
        try:
            screenshot_at(video_path, h["timestamp"], shot_path)
            screenshot_paths[h["timestamp"]] = shot_path
        except subprocess.CalledProcessError as e:
            print(f"[警告] {h['timestamp']:.0f}s截圖失敗，這個重點跳過截圖：{e}")

    pdf_path = os.path.join(output_dir, f"{name}.pdf")
    build_pdf(name, corrected_text, summary, highlights, screenshot_paths, pdf_path)
    print(f"PDF已產生：{pdf_path}")

    if not push_to_discord:
        print("[跳過推播] --no-discord，PDF已存在本機")
        return
    if not BOT_TOKEN or not CHANNEL_ID:
        print("[跳過推播] 缺少DISCORD_BOT_TOKEN或VOICE_TRANSCRIPT_CHANNEL_ID，PDF已存在本機但不推播")
        return

    thread_name = f"{name} {datetime.now():%Y-%m-%d}"
    thread_id = create_thread(thread_name)
    print(f"已建立討論串：{thread_name}（ID: {thread_id}）")

    followup = f"【修正稿】\n{corrected_text}"
    if summary:
        followup += f"\n\n【摘要】\n{summary}"
    post_message(thread_id, followup)
    post_file(thread_id, pdf_path, message="完整報告(逐字稿+摘要+重點截圖)")
    print(f"已推播完成：{name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True, help="含影片檔的資料夾")
    parser.add_argument("--no-discord", action="store_true", help="只產生本機PDF，不推播Discord(測試用)")
    args = parser.parse_args()

    videos = sorted(
        os.path.join(args.folder, f)
        for f in os.listdir(args.folder)
        if os.path.splitext(f)[1].lower() in VIDEO_EXTS
    )
    if not videos:
        print(f"{args.folder} 底下找不到支援的影片檔({', '.join(VIDEO_EXTS)})")
        return

    output_dir = os.path.join(args.folder, "digest_output")
    os.makedirs(output_dir, exist_ok=True)
    print(f"找到 {len(videos)} 支影片，PDF輸出到：{output_dir}")

    with tempfile.TemporaryDirectory(prefix="video_digest_") as work_dir:
        for video_path in videos:
            try:
                _process_one(video_path, work_dir, output_dir, push_to_discord=not args.no_discord)
            except Exception as e:
                print(f"[失敗] {os.path.basename(video_path)} 處理中斷，跳到下一支：{e}", file=sys.stderr)
                continue


if __name__ == "__main__":
    main()
