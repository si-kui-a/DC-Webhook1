# -*- coding: utf-8 -*-
"""會議/直播影片轉逐字稿(含時間戳)，重用本專案的GPU faster-whisper
large-v3 model + DLL路徑設定。2026-08-06從wedding-marketing-archive
會議記錄整理任務中抽出，正式收錄進voice_transcript底下，供之後任何
專案重複使用，不用每次在scratchpad重寫一份。

跟transcribe.py的差異：transcribe.py回傳去除時間戳的整段文字(給
Discord推播用)；這支保留每一段的start/end時間戳(給組meeting record
md + 挑截圖時間點用)。

用法：
    .venv/Scripts/python.exe meeting_transcribe.py <wav路徑> [輸出json路徑]
"""
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent

if sys.platform == "win32":
    _nvidia_dir = _HERE / ".venv" / "Lib" / "site-packages" / "nvidia"
    for _sub in ("cublas/bin", "cudnn/bin"):
        _dll_dir = _nvidia_dir / _sub
        if _dll_dir.is_dir():
            os.add_dll_directory(str(_dll_dir))
            os.environ["PATH"] = str(_dll_dir) + os.pathsep + os.environ["PATH"]

from faster_whisper import WhisperModel
from opencc import OpenCC

MODEL_PATH = str(_HERE / "models" / "faster-whisper-large-v3")
COMPUTE_TYPE = "int8_float16"

_cc = OpenCC("s2twp")
_model = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_PATH, device="cuda", compute_type=COMPUTE_TYPE)
    return _model


def transcribe_with_timestamps(wav_path: str) -> list[dict]:
    model = get_model()
    segments, _info = model.transcribe(str(wav_path), language="zh", vad_filter=True)
    out = []
    for seg in segments:
        out.append({
            "start": round(seg.start, 1),
            "end": round(seg.end, 1),
            "text": _cc.convert(seg.text.strip()),
        })
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: meeting_transcribe.py <wav路徑> [輸出json路徑]", file=sys.stderr)
        sys.exit(1)
    wav_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    segs = transcribe_with_timestamps(wav_path)
    if out_path:
        Path(out_path).write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"寫入 {len(segs)} 段到 {out_path}")
    else:
        print(json.dumps(segs, ensure_ascii=False, indent=2))
