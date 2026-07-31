"""
run_session.py — 完整語音逐字稿流程：錄音(Ctrl+C停止) -> faster-whisper
轉錄 -> 推原稿到新討論串 -> 規則式口語清理+TextRank摘要 -> 推修正稿到
同一討論串。

用法：
    .venv/Scripts/python.exe run_session.py
"""
import sys

from correct_and_summarize import correct_and_summarize
from discord_push import push_transcript
from record import record_to_wav
from transcribe import transcribe


def main():
    wav_path = record_to_wav()

    print("轉錄中...")
    raw_text = transcribe(wav_path)
    if not raw_text:
        print("轉錄結果為空(可能整段都是靜音)，不推播。", file=sys.stderr)
        return
    print(f"逐字稿原稿：\n{raw_text}\n")

    corrected_text, summary = correct_and_summarize(raw_text)
    print(f"修正稿：\n{corrected_text}\n")
    if summary:
        print(f"摘要：\n{summary}\n")

    thread_id = push_transcript(raw_text, corrected_text, summary)
    print(f"已推播完成，討論串ID：{thread_id}")


if __name__ == "__main__":
    main()
