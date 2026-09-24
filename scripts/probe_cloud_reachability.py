"""Probe which scrapers still work from a given network location.

Why: before moving the Windows scheduled tasks to GitHub Actions we need to
know whether Taiwanese sources (TWSE/MOL/104/...) block overseas runner IPs.
Calls each scraper's fetch() only -- no DB writes, no webhook pushes -- and
prints one line per scraper so a local run and a runner run can be diffed.
"""
import importlib
import sys
import time
from pathlib import Path

import truststore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
truststore.inject_into_ssl()  # same as main.py, so results match production behaviour

# (module, function) pairs; only pure fetch functions, no delivery side effects.
TARGETS = [
    ("scrapers.cbc", "fetch"),
    ("scrapers.etf0050", "fetch"),
    ("scrapers.macro_fred", "fetch"),
    ("scrapers.twse_financials", "fetch_tsmc"),
    ("scrapers.twse_financials", "fetch_chunghwa"),
    ("scrapers.internship_mol", "fetch"),
    ("scrapers.internship_104", "fetch"),
    ("scrapers.internship_yes123", "fetch"),
    ("scrapers.internship_rich", "fetch"),
    ("scrapers.internship_gift", "fetch"),
    ("scrapers.scholarship_moe", "fetch"),
    ("scrapers.scholarship_thu", "fetch"),
    ("scrapers.scholarship_efg", "fetch"),
    ("scrapers.scholarship_daad", "fetch"),
    ("scrapers.thu_calendar", "fetch_raw_events"),
    ("scrapers.semi_tw_suppliers", "fetch_all"),
    ("scrapers.semi_supply_chain", "fetch"),
    ("scrapers.tsmc", "fetch"),
    ("scrapers.fed", "fetch"),
    ("scrapers.us_customer_feeds", "fetch_all"),
    ("scrapers.substack_generic", "fetch_all"),
]

# rental_search needs RENTAL_FEED_URLS from .env, so probe its host directly.
RAW_URLS = ["https://data.moi.gov.tw/"]


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8")
    for mod_name, fn_name in TARGETS:
        start = time.monotonic()
        try:
            fn = getattr(importlib.import_module(mod_name), fn_name)
            result = f"OK n={len(fn())}"
        except Exception as e:  # report and continue; one dead source must not hide the rest
            result = f"FAIL {type(e).__name__}: {str(e)[:400]}"
        print(f"{mod_name}.{fn_name}\t{time.monotonic() - start:.1f}s\t{result}", flush=True)

    import requests
    # substack_generic swallows per-feed errors into placeholder items; surface the
    # real status, with the bot UA and with a browser UA, to tell IP blocks from UA blocks.
    feed = "https://unclestocknotes.substack.com/feed"
    for label, ua in (("bot-UA", "IntelPusher/0.2 (personal research bot)"),
                      ("browser-UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                     "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")):
        try:
            r = requests.get(feed, headers={"User-Agent": ua}, timeout=20)
            result = f"HTTP {r.status_code} server={r.headers.get('server')} cf-mitigated={r.headers.get('cf-mitigated')}"
        except Exception as e:
            result = f"FAIL {type(e).__name__}"
        print(f"substack {label}\t-\t{result}", flush=True)

    for url in RAW_URLS:
        try:
            status = requests.get(url, timeout=20).status_code
            result = f"HTTP {status}"
        except Exception as e:
            result = f"FAIL {type(e).__name__}"
        print(f"{url}\t-\t{result}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
