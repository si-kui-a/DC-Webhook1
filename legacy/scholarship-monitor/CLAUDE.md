# course-tools — Project Context
# 安裝位置: C:\Projects\course-tools\CLAUDE.md

## Stack
Pure static HTML/CSS/JS — zero build pipeline, zero npm, zero frameworks
Working dir: C:\Projects\files\

## Completed
Phase 01–03: storage.js (FNV-1a hashing), editable-table.js, list-editor.js,
courses.html, credit-calc.js, timetable.html (conflict detection),
credits.html (8-semester summary, GE domain, labor hours)

## Hard Constraint
No npm install. No build step. No external CDN unless already in existing files.
All JS must run directly in browser without transpilation.

## File Discovery
Always Read the target HTML file before modifying — inline scripts vary per page.

---

# scholarship-monitor — Project Context
# 安裝位置: C:\Projects\scholarship-monitor\CLAUDE.md

## Stack
Node.js + SQLite + node-cron + Discord webhooks (no bot token)
JSON file storage | Windows Task Scheduler

## Crawlers
MOE, DAAD, Tunghai University, European Funding Guide — real HTTP crawlers

## Rule Engine
6 exclusion categories | confidence scoring
Multilingual exclusion dictionaries: Chinese / English / German

## ACTIVE BUG (last known)
Exclusion keywords for department-specific scholarships failing to filter
in /篩選獎學金 path. Status: unresolved at last session.
Do not mark as fixed without running actual filter test.

## Scheduling
8-hour catch-up logic on cron restart — do not remove this.
