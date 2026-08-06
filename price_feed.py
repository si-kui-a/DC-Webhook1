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
from datetime import date, timedelta

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
STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"  # 個股歷史(已直接curl驗證,同etf0050.py既有做法)
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
ODD_LOT_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BFT41U"  # 盤後零股當日成交(已直接curl驗證)

# 台股全市場收盤價每個process只查一次,查完快取在記憶體——單次cron執行
# 的生命週期內重複查多檔股票不需要重打全市場快照(單次回傳約2000+檔,
# 沒必要每檔都重新下載一次)。
_twse_cache: dict[str, float] | None = None

# 個股歷史收盤價序列(供技術指標用),依代號快取——同一個process內若同一
# 檔股票被查兩次(例如持倉+開倉候選重疊),不用重複打兩次月份的API。
_tw_history_cache: dict[str, list[float]] = {}


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


_odd_lot_cache: dict[str, float] | None = None


def get_odd_lot_price(code: str) -> float | None:
    """回傳code當天盤後零股(BFT41U)的成交價——tw_stock帳戶每月定期定額
    金額很小(50~100 TWD),實際只買得起零股,應該用零股市場的實際成交價
    而非整股(STOCK_DAY_ALL)收盤價計算購買數量,兩者在盤勢劇烈時可能不同
    (2026-08-06使用者指示)。當天該檔無零股成交(TradePrice缺漏/空白)則
    回傳None,呼叫端(main._run_tw_stock_dca)應退回_get_tw_stock_price()
    的整股收盤價,不可用0頂替。"""
    global _odd_lot_cache
    if _odd_lot_cache is None:
        data = _get_with_retry(ODD_LOT_URL)
        if not isinstance(data, list):
            return None
        _odd_lot_cache = {}
        for row in data:
            try:
                _odd_lot_cache[row["Code"]] = float(row["TradePrice"])
            except (KeyError, ValueError, TypeError):
                continue
    return _odd_lot_cache.get(code)


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


def _get_tw_stock_history(code: str) -> list[float] | None:
    """抓最近2個月的日收盤價(約40+個交易日,足夠算SMA20),由舊到新排序。
    比照scrapers/etf0050.py的既有做法,但只抓2個月不是7個月——這裡只需要
    SMA20等級的短期指標,不算MA120,沒必要抓更多。"""
    if code in _tw_history_cache:
        return _tw_history_cache[code]

    this_month = date.today().replace(day=1)
    prev_month = (this_month - timedelta(days=1)).replace(day=1)
    prices: list[float] = []
    for month_start in (prev_month, this_month):
        data = _get_with_retry(STOCK_DAY_URL, params={
            "response": "json",
            "date": month_start.strftime("%Y%m%d"),
            "stockNo": code,
        })
        if not isinstance(data, dict) or data.get("stat") != "OK":
            continue
        for row in data.get("data", []):
            try:
                prices.append(float(row[6].replace(",", "")))
            except (IndexError, ValueError, AttributeError):
                continue

    if not prices:
        return None
    _tw_history_cache[code] = prices
    return prices


def _get_crypto_history(symbol: str, days: int = 20) -> list[float] | None:
    data = _get_with_retry(BINANCE_KLINES_URL, params={
        "symbol": f"{symbol.upper()}USDT",
        "interval": "1d",
        "limit": days,
    })
    if not isinstance(data, list) or not data:
        return None
    try:
        return [float(row[4]) for row in data]  # index 4 = close price
    except (IndexError, ValueError, TypeError):
        return None


def get_price_history(symbol: str) -> list[float] | None:
    """回傳symbol最近的收盤價序列(由舊到新),供技術指標計算用;查不到回傳
    None。"""
    symbol = symbol.strip()
    if not symbol:
        return None
    if symbol.isdigit():
        return _get_tw_stock_history(symbol)
    return _get_crypto_history(symbol)


def _compute_rsi(history: list[float], period: int = 14) -> float | None:
    """標準RSI(簡單移動平均版,非Wilder平滑——單次計算、不需要跨批次遞增
    更新狀態，簡單版對這裡的用途(給AI參考的離散快照)已足夠，不需要
    Wilder平滑的精確度)。需要period+1個價格(period個漲跌幅)才能算。"""
    if len(history) < period + 1:
        return None
    changes = [history[i] - history[i - 1] for i in range(-period, 0)]
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _compute_bollinger(history: list[float], period: int = 20, num_std: float = 2.0) -> dict | None:
    """布林通道(middle=SMA(period), upper/lower=middle±num_std個標準差)。"""
    if len(history) < period:
        return None
    window = history[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance ** 0.5
    return {
        "middle": mean,
        "upper": mean + num_std * std,
        "lower": mean - num_std * std,
    }


def get_technical_snapshot(symbol: str) -> dict | None:
    """回傳symbol的技術指標快照(SMA5/SMA20/5日與20日漲跌%/RSI14/布林通道),
    純程式計算不耗AI額度,給AI具體數字而非只有敘事文字可判斷。資料不足以算
    某個指標就省略該欄位,不報錯(比照scrapers/etf0050.py._check_ma_support()
    的既有降級模式)。完全查無歷史資料回傳None。"""
    history = get_price_history(symbol)
    if not history:
        return None

    snapshot: dict = {"latest": history[-1]}
    if len(history) >= 5:
        snapshot["sma5"] = sum(history[-5:]) / 5
        snapshot["change_5d_pct"] = (history[-1] - history[-5]) / history[-5] * 100
    if len(history) >= 20:
        snapshot["sma20"] = sum(history[-20:]) / 20
        snapshot["change_20d_pct"] = (history[-1] - history[-20]) / history[-20] * 100

    rsi = _compute_rsi(history)
    if rsi is not None:
        snapshot["rsi14"] = rsi

    bollinger = _compute_bollinger(history)
    if bollinger is not None:
        snapshot["bollinger_upper"] = bollinger["upper"]
        snapshot["bollinger_middle"] = bollinger["middle"]
        snapshot["bollinger_lower"] = bollinger["lower"]

    return snapshot
