"""
price_feed.py — 模擬持倉用的即時市價來源。

介面先定義出來,實際串接的場外價格API由使用者自行接上(台股/加密貨幣
即時報價,見main.py run_portfolio_channel的設計討論)。在接上之前呼叫
get_price()會丟NotImplementedError,main.py會攔截並跳過該次執行,不會
用假資料硬算PnL。
"""


def get_price(symbol: str) -> float | None:
    """回傳symbol的即時市價;查不到回傳None(呼叫端須視為「這筆先跳過」,
    不可預設0或沿用avg_cost頂替——那會讓PnL計算失真)。"""
    raise NotImplementedError(
        f"price_feed.get_price() 尚未接上實際的市價API(symbol={symbol})，"
        "請在這裡實作台股/加密貨幣的即時報價查詢"
    )
