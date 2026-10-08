# 指令目錄

<!-- make_command_catalog: dirs=.; title=指令目錄 -->
本檔由 `make_command_catalog.py` 從各腳本開頭說明自動產生，不要手動編輯；改腳本說明後執行 `python make_command_catalog.py --recheck COMMANDS.md --write`。

| 腳本 | 用途 |
|---|---|
| `add_thu_event.py` | Add a Tunghai campus-activity registration email to the reminder list (jobs/thu_events.py). |
| `auto_commit_internship_keywords.py` | scripts/auto_commit_internship_keywords.py — 每月排程，只commit+push config/internship_keywords.json一個檔案，直接進main分支，不走featur… |
| `check_encoding.py` | Fail if any tracked-area .py file is not valid UTF-8 (run by CI quality.yml). |
| `check_links.py` | Check recently fetched item links without rewriting original URLs. |
| `cloud_scheduler.py` | Hourly dispatcher for the GitHub Actions scheduler (replaces Windows Task Scheduler). |
| `dev_knowledge_audit.py` | 「資深懶散工程師複查」的機械化版本。 |
| `discord_admin.py` | scripts/discord_admin.py — Discord 伺服器頻道/webhook 管理小工具。 |
| `install_hooks.py` | 安裝 pre-commit + pre-push hook。 |
| `pre_commit_guard.py` | git commit前的強制檢查，安裝於 .git/hooks/pre-commit（見 scripts/install_hooks.py）。 |
| `pre_push_guard.py` | git push前的強制檢查，安裝於 .git/hooks/pre-push（見 scripts/install_hooks.py / install_hooks_full.py）。 |
| `probe_cloud_reachability.py` | Probe which scrapers still work from a given network location. |
| `publish_mechanical_change.py` | scripts/publish_mechanical_change.py — 把「機械性/低風險清單增減」類變更(CLAUDE.md 2026-08-07訂定、範圍限定ip與study-companion的PR自動merge例外)從「… |
| `reminder_health.py` | One command to check the whole reminder pipeline (docs/operations/提醒推播流水線.md), read-only. |
| `run_profile_checks.py` | Run profile-specific offline checks without AI or network access. |
| `show_source_status.py` | 印出目前repo實際有哪些來源/頻道(2026-09-10新增)。 |
| `verify_project_contract.py` | Offline contract check for repositories adopting the universal workflow. |

## 呼叫方式

### `add_thu_event.py`

```
python scripts/add_thu_event.py EMAIL.txt [EMAIL.txt ...] [--no-sync]
python scripts/add_thu_event.py - [--no-sync]        (email text on stdin; nothing saved but the activity)
python scripts/add_thu_event.py --list
python scripts/add_thu_event.py --sync                (re-upload the list; reminder_health.py says when)
python scripts/add_thu_event.py --remove ID [--no-sync]
```

### `auto_commit_internship_keywords.py`

```
python auto_commit_internship_keywords.py   (不需參數)
```

### `check_encoding.py`

```
python scripts/check_encoding.py
```

### `check_links.py`

```
python check_links.py [--limit] [--days]   (由參數定義推導)
```

### `cloud_scheduler.py`

```
python cloud_scheduler.py [--only] [--dry-run] [--now] [--mark-done-today] [--force]   (由參數定義推導)
```

### `dev_knowledge_audit.py`

```
`python scripts/dev_knowledge_audit.py`（純stdlib，不用裝
```

### `discord_admin.py`

```
python scripts/discord_admin.py list-channels
python scripts/discord_admin.py create-channel --name "總經指標追蹤"
python scripts/discord_admin.py create-webhook --channel-id 123456789 --name "總經指標追蹤"
python scripts/discord_admin.py delete-webhook --webhook-id 123456789
python scripts/discord_admin.py rotate-webhook --env-key WEBHOOK_INSTITUTIONAL_TSMC
```

### `install_hooks.py`

```
python install_hooks.py   (不需參數)
```

### `pre_commit_guard.py`

```
python pre_commit_guard.py   (不需參數)
```

### `pre_push_guard.py`

```
python pre_push_guard.py   (不需參數)
```

### `probe_cloud_reachability.py`

```
python probe_cloud_reachability.py   (不需參數)
```

### `publish_mechanical_change.py`

```
python scripts/publish_mechanical_change.py \
--files config/internship_keywords.json test/test_foo.py \
--branch chore/some-change \
--commit-title "chore(internship): exclude XX listings" \
--commit-body "Why..." \
--pr-title "chore(internship): 排除XX類" \
--pr-body "PR說明..." \
```

### `reminder_health.py`

```
python scripts/reminder_health.py [--days-ahead 7]
```

### `run_profile_checks.py`

```
python run_profile_checks.py [--root] [--profile] [--ai-required] [--json]   (由參數定義推導)
```

### `show_source_status.py`

```
python show_source_status.py   (不需參數)
```

### `verify_project_contract.py`

```
python verify_project_contract.py [--root] [--profile-file] [--json]   (由參數定義推導)
```
