"""
record.py — 前台錄音，Ctrl+C 停止，存成 16kHz mono wav(Whisper原生取樣率)。

用法：
    .venv/Scripts/python.exe record.py
    （Ctrl+C 停止錄音，存檔路徑印在最後一行）
"""
import queue
import sys
import wave
from datetime import datetime
from pathlib import Path

import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
RECORDINGS_DIR = Path(__file__).parent / "recordings"


def record_to_wav() -> Path:
    RECORDINGS_DIR.mkdir(exist_ok=True)
    out_path = RECORDINGS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.wav"

    q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[錄音警告] {status}", file=sys.stderr)
        q.put(indata.copy())

    print("開始錄音，按 Ctrl+C 停止...")
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SAMPLE_RATE)
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=callback,
        ):
            try:
                while True:
                    wf.writeframes(q.get().tobytes())
            except KeyboardInterrupt:
                pass

    print(f"錄音結束，已存檔：{out_path}")
    return out_path


if __name__ == "__main__":
    record_to_wav()
