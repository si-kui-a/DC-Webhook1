"""
main.py — 主執行入口。

用法：
    python main.py --source fed
    python main.py --source etf0050
    python main.py --source macro_fred
    python main.py --source twse_tsmc
    python main.py --source twse_chunghwa
    python main.py --source us_stock_digest
    python main.py --source crypto_digest
    python main.py --source macro_tech_digest
    python main.py --source geopolitics_digest
    python main.py --source tsmc_digest
    python main.py --source cbc_digest
    python main.py --source semi_supply_chain_digest
    python main.py --source tw_stock_meta
    python main.py --source crypto_meta
    python main.py --source daily_recap
    python main.py --source thu_calendar
    python main.py --source all

tsmc/cbc已從即時逐篇推播改為晚間彙整(見DIGEST_CHANNELS)，substack_easypoint
已併入us_stock_digest，三者都不再是SOURCE_REGISTRY的獨立--source選項。

排程用Windows工作排程器，非cron，見scripts/setup_scheduled_tasks.ps1。
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import truststore

truststore.inject_into_ssl()  # 修 certifi 對某些政府網站(如 cbc.gov.tw)憑證鏈驗證過嚴的問題，改用系統信任庫

from dotenv import load_dotenv

import ai_insight
import db
import index_dca_engine
import price_feed
import summarizer_en
import summarizer_zh
import notify_telegram
from push_webhook import build_embed, send_webhook
from scrapers import tsmc, fed, cbc, etf0050, macro_fred, twse_financials, rental_search
from scrapers.contracts import validate_items
from scrapers.health import record as record_source_health
from scrapers import substack_generic
from scrapers import semi_supply_chain
import digest_format
from scrapers import scholarship_daad, scholarship_moe, scholarship_thu, scholarship_efg
from scrapers import scholarship_util
from scrapers import internship_mol
from scrapers import internship_104
from scrapers import internship_rich
from scrapers import internship_yes123
from scrapers import internship_gift
from scrapers import internship_util
from scrapers import sig_content_watch

from jobs.paths import WORK_DIR
from jobs.engine import SOURCE_REGISTRY, run_source
from jobs.scholarship import run_scholarship
from jobs.internship import run_internship
from jobs.digest import DIGEST_CHANNELS, run_digest_channel
from jobs.meta_summary import META_SUMMARY_CHANNELS, run_meta_summary_channel
from jobs.daily_recap import run_daily_recap
from jobs.portfolio import PORTFOLIO_CHANNELS, run_portfolio_channel
from jobs.sig_watch import run_sig_content_watch
from jobs.crypto_recap import run_crypto_nightly_recap
from jobs.thu_calendar import run_thu_calendar
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_activity_handler = logging.FileHandler(os.path.join(WORK_DIR, "activity.log"), encoding="utf-8")
_activity_handler.setLevel(logging.INFO)
_activity_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

_error_handler = logging.FileHandler(os.path.join(WORK_DIR, "error.log"), encoding="utf-8")
_error_handler.setLevel(logging.ERROR)
_error_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    handlers=[logging.StreamHandler(), _activity_handler, _error_handler],
)
logger = logging.getLogger("main")

load_dotenv()
def main():
    parser = argparse.ArgumentParser(description="本地爬蟲 → Discord Webhook 推播")
    parser.add_argument("--source", choices=list(SOURCE_REGISTRY.keys()) + ["all", "daily_recap", "crypto_nightly_recap", "sig_content_watch", "thu_calendar"]
                                     + list(DIGEST_CHANNELS.keys()) + list(META_SUMMARY_CHANNELS.keys())
                                     + list(PORTFOLIO_CHANNELS.keys()),
                        help="執行單一來源（與 --scholarship 二選一）")
    parser.add_argument("--scholarship", action="store_true",
                        help="批次執行所有獎學金來源")
    parser.add_argument("--internship", action="store_true",
                        help="批次執行所有台灣實習來源")
    # 2026-07-31新增(排程精簡)：這三個時間點原本各自拆成多個獨立Windows
    # Scheduled Task(7個20:00+4個09:00+2個20:30=13個)，但同一時間點的
    # 來源沒有理由分開排程，改成各一個flag內部迴圈跑完，Task Scheduler
    # 裡的任務數從22個減到約11個，見docs/scheduled_task_consolidation.md。
    parser.add_argument("--digest-all", action="store_true",
                        help="批次執行所有晚間彙整頻道(20:00)")
    parser.add_argument("--meta-all", action="store_true",
                        help="批次執行所有大總結頻道(20:30)")
    parser.add_argument("--daily-official", action="store_true",
                        help="批次執行所有平日官方資料來源(fed/etf0050/macro_fred/twse_tsmc/twse_chunghwa,09:00)")
    args = parser.parse_args()

    flags = (args.source, args.scholarship, args.internship, args.digest_all, args.meta_all, args.daily_official)
    if not any(flags):
        parser.print_help()
        sys.exit(1)
    if sum(bool(x) for x in flags) > 1:
        parser.error("--source / --scholarship / --internship / --digest-all / --meta-all / --daily-official 六者互斥，一次只能選一個")

    db.init_db()

    if args.scholarship:
        run_scholarship()
    elif args.internship:
        run_internship()
    elif args.digest_all:
        # 批次迴圈本身也接一層例外(即使run_digest_channel理論上該自己接
        # 完)，避免任何未預期例外讓後面的頻道整批不執行——這條保證在改成
        # 批次flag之前是Windows Task Scheduler天然提供的(每個頻道獨立
        # process)，合併執行後要自己補上。
        for key in DIGEST_CHANNELS:
            try:
                run_digest_channel(key)
            except Exception:
                logger.error(f"[{key}] 執行時發生未預期例外，跳過此頻道", exc_info=True)
    elif args.meta_all:
        for key in META_SUMMARY_CHANNELS:
            try:
                run_meta_summary_channel(key)
            except Exception:
                logger.error(f"[{key}] 執行時發生未預期例外，跳過此頻道", exc_info=True)
    elif args.daily_official:
        # macro_fred/etf0050原本只排平日(非交易日執行只會抓到空資料靜默
        # 省略，見各自docstring既有容錯設計)，twse_tsmc/twse_chunghwa/fed
        # 本來就是Daily，統一每天執行不影響功能，順便省掉平日/每日兩種
        # 排程頻率的差異。
        for key in ("fed", "etf0050", "macro_fred", "twse_tsmc", "twse_chunghwa", "rental_search"):
            # run_source()本身已有完整try/except(既有設計)，這裡再包一層
            # 純粹是跟--digest-all/--meta-all維持同一種批次防護風格一致。
            try:
                run_source(key)
            except Exception:
                logger.error(f"[{key}] 執行時發生未預期例外，跳過此來源", exc_info=True)
    elif args.source in DIGEST_CHANNELS:
        run_digest_channel(args.source)
    elif args.source in META_SUMMARY_CHANNELS:
        run_meta_summary_channel(args.source)
    elif args.source in PORTFOLIO_CHANNELS:
        run_portfolio_channel(args.source)
    elif args.source == "daily_recap":
        run_daily_recap()
    elif args.source == "crypto_nightly_recap":
        run_crypto_nightly_recap()
    elif args.source == "sig_content_watch":
        run_sig_content_watch()
    elif args.source == "thu_calendar":
        run_thu_calendar()
    elif args.source == "all":
        for key in SOURCE_REGISTRY:
            run_source(key)
    else:
        run_source(args.source)


if __name__ == "__main__":
    sys.exit(main())
