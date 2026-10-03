#!/usr/bin/env python3
"""Run profile-specific offline checks without AI or network access."""
from __future__ import annotations
import argparse, csv, json, re, subprocess, sys
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

AI_WORDS = re.compile(r"(faster-whisper|openai|anthropic|gemini|transformers|torch|llama)", re.I)

def tracked(root: Path) -> set[str]:
    try:
        out = subprocess.check_output(["git", "-C", str(root), "ls-files"], text=True, encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return {x.replace("\\", "/") for x in out.splitlines()}
    except (OSError, subprocess.CalledProcessError):
        return set()

def archive_check(root: Path, errors: list[str], warnings: list[str]) -> None:
    manifests = [root / "MANIFEST.csv", root / "manifest.csv"]
    catalogs = [root / "CATALOG.csv", root / "catalog.csv"]
    found = next((p for p in manifests if p.is_file()), None)
    if not found: warnings.append("content-archive: missing MANIFEST.csv; add file/hash inventory")
    else:
        try:
            rows = list(csv.DictReader(found.open(encoding="utf-8-sig", newline="")))
            if not rows: errors.append(f"content-archive: empty {found.name}")
            path_key = next((k for k in ("path", "file", "relative_path") if rows and k in rows[0]), None)
            if path_key:
                missing = [r[path_key] for r in rows if r.get(path_key) and not (root / r[path_key]).is_file()]
                if missing: errors.append(f"content-archive: {len(missing)} manifest paths missing")
        except (OSError, csv.Error) as exc: errors.append(f"content-archive: cannot read {found.name}: {exc}")
    if not any(p.is_file() for p in catalogs): warnings.append("content-archive: missing CATALOG.csv")

def ai_check(root: Path, profile: str, ai_required: bool, errors: list[str], warnings: list[str]) -> None:
    deps = []
    for name in ("requirements.txt", "pyproject.toml", "package.json"):
        p = root / name
        if p.is_file():
            try: deps.append("\\n".join(line for line in p.read_text(encoding="utf-8-sig").splitlines() if not line.lstrip().startswith("#")))
            except OSError: pass
    has_ai = bool(AI_WORDS.search("\n".join(deps)))
    if has_ai and not ai_required:
        warnings.append("AI dependency detected while AI_REQUIRED=false; isolate it in optional extras")
    if ai_required and profile != "ai-native": warnings.append("AI_REQUIRED=true should use profile ai-native")
    if ai_required and not (root / "README.md").is_file(): errors.append("AI-native project needs README model/degradation instructions")

def static_check(root: Path, errors: list[str], warnings: list[str], entrypoint: str | None = None) -> None:
    index = root / (entrypoint or "index.html")
    if not index.is_file():
        if (root / "app.py").is_file(): return
        errors.append("web/static check: index.html or app.py missing") ; return
    text = index.read_text(encoding="utf-8-sig", errors="replace")
    for ref in re.findall(r"(?:src|href)=[\"']([^\"'#?]+)", text, re.I):
        if ref.startswith(("http://", "https://", "data:", "mailto:", "/")): continue
        if not (root / ref).is_file(): warnings.append(f"web/static check: referenced asset missing: {ref}")

def privacy_check(root: Path, errors: list[str], warnings: list[str]) -> None:
    files = tracked(root)
    leaked = sorted(p for p in files if Path(p).name in {".env", ".env.local"} or Path(p).name.endswith(".pem"))
    if leaked: errors.append("private-data check: secret-like files tracked: " + ", ".join(leaked[:5]))
    if not (root / ".gitignore").is_file(): warnings.append("private-data check: .gitignore missing")

def migration_check(root: Path, errors: list[str], warnings: list[str]) -> None:
    if root.name.lower().startswith("to-delete-"):
        readme = root / "README.md"
        if not readme.is_file(): errors.append("migration check: retired repository needs migration README")
        elif "migrat" not in readme.read_text(encoding="utf-8-sig", errors="replace").lower(): warnings.append("migration check: README does not mention migration")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path.cwd()); ap.add_argument("--profile", default=None)
    ap.add_argument("--ai-required", action="store_true"); ap.add_argument("--json", action="store_true")
    args = ap.parse_args(); root = args.root.resolve(); errors: list[str] = []; warnings: list[str] = []
    profile = args.profile or "unknown"
    entrypoint = None
    p = root / "PROJECT_PROFILE.yaml"
    if p.is_file():
        text = p.read_text(encoding="utf-8-sig", errors="replace").replace("`r`n", "\n")
        for line in text.splitlines():
            if line.startswith("profile:"): profile = line.split(":", 1)[1].strip().strip("\"'")
            if line.startswith("AI_REQUIRED:"): args.ai_required = line.split(":", 1)[1].strip().lower() == "true"
            if line.startswith("entrypoint:"): entrypoint = line.split(":", 1)[1].strip().strip("\"'")
    if profile == "content-archive": archive_check(root, errors, warnings)
    if profile in {"ai-native", "service-automation", "gui-media-tool"}: ai_check(root, profile, args.ai_required, errors, warnings)
    if profile == "web-platform": static_check(root, errors, warnings, entrypoint)
    privacy_check(root, errors, warnings); migration_check(root, errors, warnings)
    result = {"status": "FAILED_CORE" if errors else ("DEGRADED" if warnings else "SUCCESS_CORE"), "profile": profile, "errors": errors, "warnings": warnings}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else f"{result['status']}: profile={profile} errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0

if __name__ == "__main__": sys.exit(main())
