"""Atomic, dependency-free source health recording."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def record(path: str | Path, source_id: str, status: str, *, item_count: int = 0,
           rejected_count: int = 0, error: str | None = None) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        data = {}
    sources = data.setdefault("sources", {})
    entry = {
        "status": status,
        "item_count": int(item_count),
        "rejected_count": int(rejected_count),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        entry["error"] = str(error)[:500]
    sources[source_id] = entry
    data["updated_at"] = entry["checked_at"]
    fd, temp_name = tempfile.mkstemp(prefix="source_health.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return entry