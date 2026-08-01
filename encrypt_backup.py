"""
encrypt_backup.py — 備份前加密含 PII 的檔案。

原則：金鑰只存本地（~/.intel-pusher-backup.key，權限600），永不進版控、永不備份。
若金鑰遺失，歷史加密備份無法還原——這是刻意的權衡，金鑰進了備份，加密就沒意義。

用法：
    python encrypt_backup.py
會將 PII_FILES 清單中存在的檔案，逐一加密輸出為同路徑 + .enc。
"""
import os
from pathlib import Path
from cryptography.fernet import Fernet

KEY_PATH = Path.home() / ".intel-pusher-backup.key"

# 含個資、需加密後才可備份的檔案清單。
# 2026-08-01新增：resume_draft.txt(履歷初版純文字稿)、past_experience.md
# (過往經歷累積紀錄)——career repo整合功能的暫存資料，透過resume_bot.py
# 的Discord文字指令維護，見memory: project_career_repo_scraper_integration_plan。
PII_FILES: list[str] = ["resume_draft.txt", "past_experience.md"]


def get_or_create_key() -> bytes:
    if not KEY_PATH.exists():
        key = Fernet.generate_key()
        KEY_PATH.write_bytes(key)
        os.chmod(KEY_PATH, 0o600)
        print(f"已產生新金鑰：{KEY_PATH}（請自行異地備份此金鑰，遺失即無法解密歷史備份）")
    return KEY_PATH.read_bytes()


def encrypt_file(src_path: Path, fernet: Fernet):
    if not src_path.exists():
        return
    data = src_path.read_bytes()
    encrypted = fernet.encrypt(data)
    dst_path = src_path.with_suffix(src_path.suffix + ".enc")
    dst_path.write_bytes(encrypted)
    print(f"已加密：{src_path.name} -> {dst_path.name}")


def main():
    fernet = Fernet(get_or_create_key())
    base_dir = Path(__file__).parent
    for filename in PII_FILES:
        encrypt_file(base_dir / filename, fernet)


if __name__ == "__main__":
    main()
