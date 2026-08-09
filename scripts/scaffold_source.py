"""Create a review-gated source adapter and manifest entry."""
from __future__ import annotations
import argparse
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "source_adapter.py"
def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--source-id", required=True); ap.add_argument("--kind", choices=["rss","json","csv","html"], required=True); ap.add_argument("--endpoint", required=True); args=ap.parse_args()
    root=Path.cwd(); target=root / "scrapers" / (args.source_id.replace(".", "_").replace("-", "_") + ".py"); text=TEMPLATE.read_text(encoding="utf-8").replace("__SOURCE_ID__",args.source_id).replace("__KIND__",args.kind).replace("__ENDPOINT__",args.endpoint); target.write_text(text,encoding="utf-8")
    manifest=root / "config" / "SOURCE_MANIFEST.yaml"; old=manifest.read_text(encoding="utf-8"); entry=f"  - source_id: {args.source_id}\n    kind: {args.kind}\n    endpoint: {args.endpoint}\n    status: PENDING_REVIEW\n"; manifest.write_text(old.replace("sources: []", "sources:\n"+entry),encoding="utf-8"); print(f"created {target}; status=PENDING_REVIEW"); return 0
if __name__ == "__main__": raise SystemExit(main())