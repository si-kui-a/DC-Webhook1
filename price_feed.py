"""
price_feed.py — 模擬持倉用的即時市價來源。

台股:TWSE官方STOCK_DAY_ALL(開放資料,免key,無robots限制,已直接curl驗證)
     回傳當天全市場收盤價。tw_stock帳戶只在收盤後(13:30後)跑一次,收盤價
     即為需要的「當下市價」,不需要真正的盤中即時報價——盤中即時報價
     (如mis.twse.com.tw)是TWSE未正式開放、robots.txt明確禁止的內部端點,
     不採用(見Meta_Dev_Knowledge.md PAT-11)。
幣圈:Binance公開行情API(免key,官方文件本來就是給程式化查價用的,已直接
     curl驗證),即時成交價,符合crypto_futures/crypto_discretionary帳戶
     每小時查詢的需求。

symbol格式決定要查哪個來源:純數字(如"2330")視為台股代號查TWSE;其餘
(如"BTC")視為加密貨幣,查Binance的{symbol}USDT交易對。這跟兩類帳戶各自
的交易範圍(台股帳戶只交易台股代號,幣圈帳戶只交易加密貨幣代號)天然一致,
不需要額外傳入資產類別參數。

get_price()查不到/查詢失敗一律回傳None,不拋例外——呼叫端(main.py)已經
用「None就跳過這筆」的邏輯處理,不可用0或其他預設值頂替(會讓PnL計算失真)。
"""
import logging
import time

import requests

logger = logging.getLogger("price_feed")

HEADERS = {
    "User-Agent": (
        "IntelPusher/0.2 (personal research bot; paper-trading price lookup)"
    ),
}
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2
TIMEOUT_SECONDS = 10

STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"

# 台股全市場收盤價每個process只查一次,查完快取在記憶體——單次cron執行
# 的生命週期內重複查多檔股票不需要重打全市場快照(單次回傳約2000+檔,
# 沒必要每檔都重新下載一次)。
_twse_cache: dict[str, float] | None = None


def _get_with_retry(url: str, params: dict | None = None) -> dict | list | None:
    """對429/5xx/連線錯誤指數退避重試,4xx(除429外)直接放棄不重試。"""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                logger.warning("price_feed 連線失敗（已重試%d次）：%s", MAX_RETRIES, e)
                return None
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_RETRIES - 1:
                logger.warning("price_feed HTTP %s（已重試%d次）：%s", resp.status_code, MAX_RETRIES, url)
                return None
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
            continue

        if resp.status_code != 200:
            return None

        try:
            return resp.json()
        except ValueError:
            return None
    return None  # pragma: no cover


def _get_tw_stock_price(code: str) -> float | None:
    global _twse_cache
    if _twse_cache is None:
        data = _get_with_retry(STOCK_DAY_ALL_URL)
        if not isinstance(data, list):
            return None
        _twse_cache = {}
        for row in data:
            try:
                _twse_cache[row["Code"]] = float(row["ClosingPrice"])
            except (KeyError, ValueError, TypeError):
                continue
    return _twse_cache.get(code)


def _get_crypto_price(symbol: str) -> float | None:
    data = _get_with_retry(BINANCE_TICKER_URL, params={"symbol": f"{symbol.upper()}USDT"})
    if not isinstance(data, dict) or "price" not in data:
        return None
    try:
        return float(data["price"])
    except (TypeError, ValueError):
        return None


def get_price(symbol: str) -> float | None:
    """回傳symbol的市價(台股為當日收盤價,加密貨幣為即時成交價);查不到或
    查詢失敗一律回傳None。"""
    symbol = symbol.strip()
    if not symbol:
        return None
    if symbol.isdigit():
        return _get_tw_stock_price(symbol)
    return _get_crypto_price(symbol)
