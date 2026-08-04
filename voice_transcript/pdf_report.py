"""
pdf_report.py — 逐字稿+摘要+重點截圖 -> 一份PDF。用fpdf2(純Python、免
系統原生依賴，Windows上裝weasyprint這類需要GTK/cairo的套件常常裝不起來，
fpdf2 pip install就能用，符合「低成本、低維護」原則)。

中文字型：fpdf2內建字型只有拉丁字母，需要embed一份支援中文的TTF——沿用
wvt/jianying-editor既有在用的NotoSansTC-Variable.ttf，同一台機器上已經
存在，不用另外找字型檔。
"""
import os

from fpdf import FPDF, XPos, YPos

FONT_PATH = "C:/Users/User/.claude/skills/jianying-editor/fonts/NotoSansTC-Variable.ttf"


def _line(pdf: FPDF, h: float, text: str):
    """multi_cell的預設new_x=XPos.RIGHT——游標留在剛印完那行的右端，不是
    傳統排版習慣的「換行回到左邊界」，2026-08-05實測撞過「Not enough
    horizontal space」這個錯誤(第二次呼叫時游標已經卡在右邊界附近)。
    這裡固定包成LMARGIN/NEXT，之後所有段落統一走這個函式，不直接呼叫
    pdf.multi_cell()，避免漏加參數重現同一個bug。"""
    pdf.multi_cell(0, h, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_pdf(title: str, corrected_text: str, summary: str | None,
              highlights: list[dict], screenshot_paths: dict, out_path: str):
    """highlights: select_highlights.py的輸出。screenshot_paths：
    {timestamp: 截圖檔路徑}，由video_digest.py先截好圖再傳進來(這支模組
    只負責排版，不碰ffmpeg)。"""
    pdf = FPDF()
    pdf.add_font("NotoTC", "", FONT_PATH)
    pdf.add_font("NotoTC", "B", FONT_PATH)  # 同一個可變字重檔充當粗體，沒有另外的Bold檔案
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.add_page()
    pdf.set_font("NotoTC", "B", 18)
    _line(pdf, 10, title)
    pdf.ln(4)

    if summary:
        pdf.set_font("NotoTC", "B", 13)
        _line(pdf, 8, "摘要")
        pdf.set_font("NotoTC", "", 11)
        _line(pdf, 7, summary)
        pdf.ln(4)

    if highlights:
        pdf.set_font("NotoTC", "B", 13)
        _line(pdf, 8, "重點環節")
        for h in highlights:
            pdf.set_font("NotoTC", "B", 11)
            m, s = divmod(int(h["timestamp"]), 60)
            _line(pdf, 7, f"{m:02d}:{s:02d} — {h.get('reason', '')}")
            shot = screenshot_paths.get(h["timestamp"])
            if shot and os.path.exists(shot):
                pdf.image(shot, w=120)
            pdf.ln(2)
        pdf.ln(2)

    pdf.set_font("NotoTC", "B", 13)
    _line(pdf, 8, "完整逐字稿")
    pdf.set_font("NotoTC", "", 10)
    _line(pdf, 6, corrected_text)

    pdf.output(out_path)
    return out_path
