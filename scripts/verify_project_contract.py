#!/usr/bin/env python3
"""Offline contract check for repositories adopting the universal workflow."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

PROFILES = {"service-automation", "gui-media-tool", "content-archive", "web-platform", "ai-native", "governance-toolkit", "library-source", "automation-bot"}
REQUIRED = ("display_name", "profile", "AI_REQUIRED", "offline_check", "run_command", "rollback", "owner")

def parse(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("`r`n", "\n")
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)", line)
        if not m:
            raise ValueError(f"{path}:{n}: expected simple key: value")
        data[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return data

def check_offline_check_reachable(offline_check: str, root: Path, errors: list[str]) -> None:
    """offline_check being a non-empty string only proves someone typed
    something -- it does not prove the command can run. 2026-09-11: found
    the *same* placeholder text (`python scripts/verify_project_contract.py
    && python scripts/run_profile_checks.py`) copy-pasted verbatim, never
    customized, into 5 separate consumer repos -- neither script actually
    existed in any of them, so the "documented offline check command" had
    silently been a no-op lie since each repo's creation. This validator
    passed every one of those repos anyway, because it only checked the
    field was non-empty. Close that gap: any `scripts/<name>.<ext>` token
    mentioned in the command must resolve to a real file under --root.
    """
    for m in re.finditer(r"\bscripts/[\w.-]+\.(?:py|js|mjs|cjs|sh|ps1)\b", offline_check):
        rel = m.group(0)
        if not (root / rel).is_file():
            errors.append(f"offline_check references a file that does not exist: {rel} "
                          f"(placeholder text left uncustomized, or the file was never copied in)")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--profile-file", default="PROJECT_PROFILE.yaml")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve(); profile_path = root / args.profile_file
    errors: list[str] = []; warnings: list[str] = []
    if not profile_path.is_file():
        errors.append(f"missing {args.profile_file}; copy templates/PROJECT_PROFILE.yaml")
        data = {}
    else:
        try: data = parse(profile_path)
        except (OSError, ValueError) as exc: errors.append(str(exc)); data = {}
    for key in REQUIRED:
        if not data.get(key): errors.append(f"missing or empty key: {key}")
    if data.get("profile") not in PROFILES: errors.append(f"profile must be one of: {', '.join(sorted(PROFILES))}")
    if data.get("AI_REQUIRED", "").lower() not in {"true", "false"}: errors.append("AI_REQUIRED must be true or false")
    if not (root / "README.md").is_file(): warnings.append("README.md is missing")
    if data.get("AI_REQUIRED", "").lower() == "true" and data.get("profile") != "ai-native":
        warnings.append("AI_REQUIRED=true should normally use profile ai-native")
    if data.get("offline_check"): check_offline_check_reachable(data["offline_check"], root, errors)
    result = {"status": "FAILED_CORE" if errors else ("DEGRADED" if warnings else "SUCCESS_CORE"), "root": str(root), "profile": data.get("profile"), "errors": errors, "warnings": warnings}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else f"{result['status']}: profile={result['profile'] or 'unknown'} errors={len(errors)} warnings={len(warnings)}")
    for item in errors: print(f"ERROR: {item}", file=sys.stderr)
    for item in warnings: print(f"WARN: {item}", file=sys.stderr)
    return 1 if errors else 0

if __name__ == "__main__": sys.exit(main())
