"""
transcribe.py — faster-whisper(large-v3, GPU) 語音轉文字 + 簡轉繁。

已驗證(2026-07-31)：RTX 4060(8GB VRAM) + ctranslate2 4.8.1 抓得到GPU
(cuda device count=1)。compute_type選int8_float16而非float16——large-v3
在float16下實測對8GB顯存偏緊，int8_float16量化後準確度損失很小，但顯存
需求明顯降低，優先求「不會中途OOM」。

Whisper中文轉錄預設偏簡體字輸出(即使腔調是台灣口音)，一律過一次OpenCC
s2twp(簡體->繁體，含台灣慣用詞彙轉換，不只是簡繁字形轉換)。

用法：
    .venv/Scripts/python.exe transcribe.py recordings/xxx.wav
"""
import sys
from pathlib import Path

from faster_whisper import WhisperModel
from opencc import OpenCC

# 直接指向本機手動下載的模型檔(見models/faster-whisper-large-v3/)，不透過
# faster-whisper預設的huggingface_hub自動下載——這個環境連不到huggingface.co
# (2026-07-31確認，防火牆/防毒層級擋掉，非程式問題)，改由使用者手動下載
# 5個檔案(config.json/model.bin/preprocessor_config.json/tokenizer.json/
# vocabulary.json)放進這個資料夾。
MODEL_PATH = str(Path(__file__).parent / "models" / "faster-whisper-large-v3")
COMPUTE_TYPE = "int8_float16"

_cc = OpenCC("s2twp")
_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_PATH, device="cuda", compute_type=COMPUTE_TYPE)
    return _model


def transcribe(wav_path: str | Path) -> str:
    """回傳整段逐字稿(繁體中文，已去除段落時間戳，純文字)。"""
    model = _get_model()
    segments, _info = model.transcribe(
        str(wav_path),
        language="zh",
        vad_filter=True,  # 過濾靜音段，避免空白處被辨識成幻覺文字
    )
    raw_text = "".join(seg.text for seg in segments).strip()
    return _cc.convert(raw_text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python transcribe.py <wav檔路徑>", file=sys.stderr)
        sys.exit(1)
    text = transcribe(sys.argv[1])
    print(text)
