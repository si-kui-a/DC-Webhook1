"""
show_source_status.py — 印出目前repo實際有哪些來源/頻道(2026-09-10新增)。

取代README.md原本手寫的「已完成 vs 尚未完成」表格——那份表格是專案第一天
MVP骨架時期寫的(還在講「沙盒環境連不上網站」「selector待實測」)，一個多月
來所有來源早就上線，但表格從沒人手動更新過，變成主動誤導。這支腳本直接
從jobs/*.py的registry dict讀取，永遠反映當下實際狀態，不需要手動維護、
不會因為忘記更新而說謊。

不涵蓋排程狀態(哪個來源實際排了、多久跑一次)——那是Windows Task
Scheduler的事，查`Get-ScheduledTask -TaskName "IntelPusher-*"`，本腳本
只回答「程式碼裡目前有哪些來源/頻道」。
"""
from __future__ import annotations

import sys
from pathlib import Path

# PAT-29：跟scripts/check_links.py同一個坑——用`python scripts/
# show_source_status.py`這種(這支腳本自己docstring暗示的)自然呼叫方式
# 執行時，sys.path[0]是scripts/不是repo根目錄，repo根目錄的jobs套件
# 直接ModuleNotFoundError，2026-09-10新增時沒補這行，check_links.py
# 已經有的修法沒有跟著套用過去。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobs.engine import SOURCE_REGISTRY
from jobs.digest import DIGEST_CHANNELS
from jobs.portfolio import PORTFOLIO_CHANNELS
from jobs.scholarship import SCHOLARSHIP_REGISTRY
from jobs.internship import INTERNSHIP_REGISTRY

# main.py裡沒有走上面任一個registry dict、各自獨立一個--source選項的來源，
# 手動列在這裡——這幾個是main.py的argparse本身就寫死的特例分派，沒有
# 共用registry可以內省，數量少且穩定，比為了3-4個項目额外設計一層抽象
# 來源更簡單直接。main.py新增這類特例時記得順手補一行。
STANDALONE_SOURCES = {
    "daily_recap": "每日晨間快報(彙整昨日3個彙整頻道)",
    "crypto_nightly_recap": "幣圈模擬持倉夜間回顧",
    "thu_calendar": "東海大學當期學期行事曆合併+Telegram提醒",
    "thu_lixue": "東海大學勵學基金申請時程Telegram提醒(前60/30/7/1天＋新公告)",
}


def _print_section(title: str, rows: list[tuple[str, str]]):
    print(f"\n## {title}（{len(rows)}）")
    for key, label in rows:
        print(f"  --source {key:<28} {label}")


def main():
    _print_section("即時逐篇推播(SOURCE_REGISTRY)", [
        (key, info[2]) for key, info in SOURCE_REGISTRY.items()
    ])
    _print_section("晚間彙整頻道(DIGEST_CHANNELS)", [
        (key, info["channel_title"]) for key, info in DIGEST_CHANNELS.items()
    ])
    _print_section("模擬持倉頻道(PORTFOLIO_CHANNELS)", [
        (key, info["channel_title"]) for key, info in PORTFOLIO_CHANNELS.items()
    ])
    _print_section("獎學金來源(SCHOLARSHIP_REGISTRY，--scholarship批次執行)", [
        (key, info[1]) for key, info in SCHOLARSHIP_REGISTRY.items()
    ])
    _print_section("實習/求職來源(INTERNSHIP_REGISTRY，--internship批次執行)", [
        (key, info[1]) for key, info in INTERNSHIP_REGISTRY.items()
    ])
    _print_section("獨立來源(main.py特例分派，非registry)", list(STANDALONE_SOURCES.items()))

    total = (len(SOURCE_REGISTRY) + len(DIGEST_CHANNELS)
             + len(PORTFOLIO_CHANNELS) + len(SCHOLARSHIP_REGISTRY) + len(INTERNSHIP_REGISTRY)
             + len(STANDALONE_SOURCES))
    print(f"\n共 {total} 個來源/頻道。完整用法：python main.py --help")


if __name__ == "__main__":
    main()
