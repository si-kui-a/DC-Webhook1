"""
scrapers/semi_supply_chain.py — 半導體與 AI 供應鏈（#semi-ai-supply-chain）。

狀態：STUB，尚未確認穩定來源。

已知候選來源與問題：
- DIGITIMES：內容多數需付費訂閱才能看全文，公開頁僅有標題，且反爬防護較強，
  屬第4節風險登錄表「C級來源」等級，不建議列為 Phase 0 核心來源。
- MOPS（公開資訊觀測站）：可抓取上市公司重大訊息公告，穩定性高（A級），
  但需要先建立「哪些公司代碼算作半導體前中段供應鏈」的白名單
  （例：日月光3711、京元電2449、弘塑3131、萬潤6187等），這份白名單
  本骨架尚未提供，需要你確認範圍後才能實作。

本檔案先留空函式與介面，避免在來源未確認前產出會誤導的假資料。
"""


def fetch() -> list[dict]:
    """
    尚未實作。實作前必須先決定：
      1. 走 MOPS 重大訊息公告（需要公司代碼白名單）
      2. 或走 DIGITIMES 公開標題頁（需接受內容僅有標題、無全文的限制）
    決定後補上與 fed.py / cbc.py 相同模式的 requests + BeautifulSoup 實作。
    """
    raise NotImplementedError(
        "半導體供應鏈來源尚未確認，見本檔案 docstring。"
        "請先決定 MOPS 白名單或 DIGITIMES 標題頁，再補實作。"
    )
