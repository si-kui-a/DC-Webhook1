"""
main.py — 主執行入口。

用法：
    python main.py --source fed
    python main.py --source etf0050
    python main.py --source macro_fred
    python main.py --source twse_tsmc
    python main.py --source twse_chunghwa
    python main.py --source us_macro_digest
    python main.py --source crypto_digest
    python main.py --source tw_semi_digest
    python main.py --source daily_recap
    python main.py --source thu_calendar
    python main.py --source thu_lixue
    python main.py --source thu_events
    python main.py --source precise_verify
    python main.py --send-at JOB KEY AT
    python main.py --source all

tsmc/cbc已從即時逐篇推播改為晚間彙整(見DIGEST_CHANNELS)，substack_easypoint
已併入us_stock_digest，三者都不再是SOURCE_REGISTRY的獨立--source選項。

排程由GitHub Actions的scheduler.yml執行(scripts/cloud_scheduler.py)，本機不再排程。
"""
import argparse
import logging
import os
import sys

import truststore

truststore.inject_into_ssl()  # 修 certifi 對某些政府網站(如 cbc.gov.tw)憑證鏈驗證過嚴的問題，改用系統信任庫

from dotenv import load_dotenv

import db

from jobs.paths import WORK_DIR
from jobs.engine import SOURCE_REGISTRY, run_source
from jobs.scholarship import run_scholarship
from jobs.internship import run_internship
from jobs.digest import DIGEST_CHANNELS, run_digest_channel
from jobs.daily_recap import run_daily_recap
from jobs.portfolio import PORTFOLIO_CHANNELS, run_portfolio_channel
from jobs.crypto_recap import run_crypto_nightly_recap
from jobs.thu_calendar import run_thu_calendar
from jobs.thu_lixue import run_thu_lixue
from jobs.thu_events import run_thu_events
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
    parser.add_argument("--source", choices=list(SOURCE_REGISTRY.keys()) + ["all", "daily_recap", "crypto_nightly_recap", "thu_calendar", "thu_lixue", "thu_events", "precise_verify"]
                                     + list(DIGEST_CHANNELS.keys())
                                     + list(PORTFOLIO_CHANNELS.keys()),
                        help="執行單一來源（與 --scholarship 二選一）")
    parser.add_argument("--scholarship", action="store_true",
                        help="批次執行所有獎學金來源")
    parser.add_argument("--internship", action="store_true",
                        help="批次執行所有台灣實習來源")
    parser.add_argument("--send-at", nargs=3, metavar=("JOB", "KEY", "AT"),
                        help="(.github/workflows/send_at.yml) 睡到 AT 再送出 JOB 的提醒")
    args = parser.parse_args()

    if args.send_at:
        from jobs.precise_send import run_send_at
        return 0 if run_send_at(*args.send_at) else 1

    flags = (args.source, args.scholarship, args.internship)
    if not any(flags):
        parser.print_help()
        sys.exit(1)
    if sum(bool(x) for x in flags) > 1:
        parser.error("--source / --scholarship / --internship 三者互斥，一次只能選一個")

    db.init_db()

    if args.scholarship:
        run_scholarship()
    elif args.internship:
        run_internship()
    # Single-channel runs (what the cloud scheduler uses) report an explicit False as
    # exit 1, so the dispatcher retries on a later tick instead of recording success.
    elif args.source in DIGEST_CHANNELS:
        return 1 if run_digest_channel(args.source) is False else 0
    elif args.source in PORTFOLIO_CHANNELS:
        run_portfolio_channel(args.source)
    elif args.source == "daily_recap":
        return 1 if run_daily_recap() is False else 0
    elif args.source == "crypto_nightly_recap":
        run_crypto_nightly_recap()
    elif args.source == "thu_calendar":
        return 1 if run_thu_calendar() is False else 0
    elif args.source == "precise_verify":
        from jobs.precise_send import run_verify
        return 1 if run_verify() is False else 0
    elif args.source == "thu_lixue":
        # explicit False (fetch or send failed) -> exit 1 so the cloud scheduler retries
        return 1 if run_thu_lixue() is False else 0
    elif args.source == "thu_events":
        return 1 if run_thu_events() is False else 0
    elif args.source == "all":
        for key in SOURCE_REGISTRY:
            run_source(key)
    else:
        run_source(args.source)


if __name__ == "__main__":
    sys.exit(main())
