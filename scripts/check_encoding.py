from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
errors = []
for path in root.rglob("*.py"):
    if any(part in {".git", "venv", "__pycache__"} for part in path.parts):
        continue
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{path}: {exc}")
if errors:
    print("UTF-8 check failed")
    print("\n".join(errors))
    sys.exit(1)
print("UTF-8 check passed")
