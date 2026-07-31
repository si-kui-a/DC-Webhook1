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


def record_to_wav(duration_seconds: float | None = None) -> Path:
    """duration_seconds為None(預設)時是正常使用情境：前台錄音、Ctrl+C停止。
    傳入秒數時改成錄固定時長就自動停止，只用於自動化測試(不需要人在場按
    Ctrl+C)，不是正式流程的一部分。"""
    import time

    RECORDINGS_DIR.mkdir(exist_ok=True)
    out_path = RECORDINGS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.wav"

    q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[錄音警告] {status}", file=sys.stderr)
        q.put(indata.copy())

    if duration_seconds is None:
        print("開始錄音，按 Ctrl+C 停止...")
    else:
        print(f"開始錄音，{duration_seconds}秒後自動停止(測試模式)...")

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
            start = time.monotonic()
            try:
                while duration_seconds is None or time.monotonic() - start < duration_seconds:
                    wf.writeframes(q.get(timeout=1).tobytes())
            except KeyboardInterrupt:
                pass
            except queue.Empty:
                pass

    print(f"錄音結束，已存檔：{out_path}")
    return out_path


if __name__ == "__main__":
    record_to_wav()
