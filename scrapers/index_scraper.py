"""
scrapers/index_scraper.py — DXY(美元指數)+ TWII(台股加權指數)快照。
2026-08-05從finfeed併入(比對daily-report五個資料源，唯一ip沒有對應的
一塊，其餘四個都已有更完整的既有任務涵蓋，見commit說明)。

資料源: yfinance
"""

import time
from typing import Callable, Any

import yfinance as yf
import pandas as pd

DXY_TICKER = "DX-Y.NYB"   # ICE美元指數
TWII_TICKER = "^TWII"     # 台股加權指數

MAX_RETRIES = 3
RETRY_INTERVAL_SEC = 5


def _fetch_with_retry(fetch_fn: Callable[[], Any], label: str) -> Any:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch_fn()
        except Exception as e:
            last_err = e
            print(f"[index_scraper] {label} 第{attempt}次失敗: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL_SEC)
    raise RuntimeError(f"[index_scraper] {label} 重試{MAX_RETRIES}次後仍失敗") from last_err


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """新版yfinance可能回傳MultiIndex欄位,強制攤平"""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_index_daily(ticker: str, days: int = 30) -> pd.DataFrame:
    """抓取指定指數近N日日線資料"""
    def _fetch():
        df = yf.download(ticker, period=f"{days}d", auto_adjust=True, progress=False)
        if df is None or df.empty:
            raise ValueError(f"{ticker} 資料為空")
        return _flatten_columns(df)

    return _fetch_with_retry(_fetch, label=f"index_daily:{ticker}")


def get_latest_value(ticker: str, days: int = 30) -> dict:
    """取得最新收盤值 + 較前一交易日漲跌幅"""
    df = fetch_index_daily(ticker, days=days)
    close = df["Close"].squeeze()

    latest_date = close.index[-1]
    latest_value = float(close.iloc[-1])
    prev_value = float(close.iloc[-2]) if len(close) >= 2 else None
    change_pct = round((latest_value - prev_value) / prev_value * 100, 4) if prev_value else None

    return {
        "ticker": ticker,
        "date": str(latest_date.date()),
        "value": round(latest_value, 4),
        "prev_value": round(prev_value, 4) if prev_value else None,
        "change_pct": change_pct,
    }


def get_dxy_twii_snapshot() -> dict:
    """每日快照用: 同時取得DXY與TWII最新數值。"""
    dxy = get_latest_value(DXY_TICKER)
    twii = get_latest_value(TWII_TICKER)

    return {
        "dxy": dxy,
        "twii": twii,
    }


if __name__ == "__main__":
    snapshot = get_dxy_twii_snapshot()
    import json
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
