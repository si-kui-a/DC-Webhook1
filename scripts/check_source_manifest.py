"""Validate source manifest and enforce the PENDING_REVIEW gate."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

KINDS = {"rss", "json", "csv", "html", "rental"}
STATUSES = {"PENDING_REVIEW", "ENABLED", "DISABLED"}

def parse(path: Path) -> list[dict]:
    rows = []
    current = None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line in {"sources: []", "sources:"} or line.startswith("version:"):
            continue
        if line.startswith("- "):
            if current: rows.append(current)
            current = {}
            line = line[2:].strip()
        if ":" in line and current is not None:
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip().strip('"\'')
    if current: rows.append(current)
    return rows

def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--root", type=Path, default=Path.cwd()); ap.add_argument("--json", action="store_true"); args = ap.parse_args()
    path = args.root / "config" / "SOURCE_MANIFEST.yaml"; errors=[]; warnings=[]; rows=[]
    if not path.is_file(): errors.append("missing config/SOURCE_MANIFEST.yaml")
    else:
        rows=parse(path); seen=set()
        for i,row in enumerate(rows):
            sid=row.get("source_id", "")
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", sid): errors.append(f"source[{i}] invalid source_id")
            if sid in seen: errors.append(f"source[{i}] duplicate source_id: {sid}")
            seen.add(sid)
            if row.get("kind") not in KINDS: errors.append(f"source[{sid}] kind must be one of {sorted(KINDS)}")
            if not row.get("endpoint", "").startswith(("https://", "http://")): errors.append(f"source[{sid}] endpoint must be http(s)")
            status=row.get("status", "PENDING_REVIEW")
            if status not in STATUSES: errors.append(f"source[{sid}] invalid status")
            if status == "ENABLED":
                for key in ("fixture", "test", "license"):
                    if not row.get(key): errors.append(f"source[{sid}] ENABLED requires {key}")
                if row.get("license") in {"UNKNOWN", ""}: errors.append(f"source[{sid}] ENABLED requires verified license")
            elif status == "PENDING_REVIEW": warnings.append(f"source[{sid}] pending review")
    result={"status":"FAILED_CORE" if errors else ("DEGRADED" if warnings else "SUCCESS_CORE"),"sources":len(rows),"errors":errors,"warnings":warnings}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 1 if errors else 0
if __name__ == "__main__": raise SystemExit(main())