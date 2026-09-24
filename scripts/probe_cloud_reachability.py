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
    # Variants that other projects report working from GitHub runners.
    chrome = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
    rss_accept = "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
    bing = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
    variants = [
        ("requests bot-UA", "https://unclestocknotes.substack.com/feed", {"User-Agent": "IntelPusher/0.2"}),
        ("requests chrome+rss-accept", "https://unclestocknotes.substack.com/feed",
         {"User-Agent": chrome, "Accept": rss_accept}),
        ("requests chrome+rss-accept /feed.xml", "https://unclestocknotes.substack.com/feed.xml",
         {"User-Agent": chrome, "Accept": rss_accept}),
        ("requests bingbot-UA", "https://unclestocknotes.substack.com/feed", {"User-Agent": bing}),
        ("requests custom-domain maxcrypto", "https://www.maxcrypto.space/feed",
         {"User-Agent": chrome, "Accept": rss_accept}),
        ("requests api/v1/archive", "https://unclestocknotes.substack.com/api/v1/archive?sort=new&limit=5",
         {"User-Agent": chrome, "Accept": "application/json"}),
        ("daad chrome", "https://www2.daad.de/bundles/daadstipendiendatenbanklsh/data/a/js/scholarships.js",
         {"User-Agent": chrome}),
    ]
    for label, url, headers in variants:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            body = r.text[:60].replace("\n", " ")
            result = f"HTTP {r.status_code} cf-mitigated={r.headers.get('cf-mitigated')} body={body!r}"
        except Exception as e:
            result = f"FAIL {type(e).__name__}"
        print(f"{label}\t-\t{result}", flush=True)

    # Same request through curl (different TLS stack) and curl_cffi (Chrome TLS fingerprint).
    import subprocess
    out = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-A", chrome,
                          "-H", f"Accept: {rss_accept}", "https://unclestocknotes.substack.com/feed"],
                         capture_output=True, text=True, timeout=30).stdout
    print(f"curl chrome+rss-accept\t-\tHTTP {out}", flush=True)
    try:
        from curl_cffi import requests as cffi
        r = cffi.get("https://unclestocknotes.substack.com/feed", impersonate="chrome", timeout=20)
        print(f"curl_cffi impersonate=chrome\t-\tHTTP {r.status_code} body={r.text[:60]!r}", flush=True)
    except ImportError:
        print("curl_cffi\t-\tnot installed", flush=True)

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
