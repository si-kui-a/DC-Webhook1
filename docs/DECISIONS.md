# 設計決策記錄

## 2026-09-10：教育類Telegram通知移除 + 東海行事曆合併job

**狀態**：已實作，等待人工review後merge到main。

**背景**：使用者確認拿掉scholarship.py/internship.py(獎學金/實習/求職三個
內容)呼叫EDU bot的Telegram簡短通知，改用同一支EDU bot排程額度做「東海
大學當期學期行事曆合併進倉庫」，每天早上6點觸發。sig_watch.py(留德網站
內容監測)未被提及，維持原樣繼續用Telegram(它是唯一通知管道，沒有Discord
webhook可以退)。

**行事曆資料來源**：官網行事曆頁
(https://fsis.thu.edu.tw/wwwstud/info/Calendar.php)本身只是外殼，實際
內容是iframe內嵌Google Calendar公開行事曆(id:
`qpejvbuas1qpq9ugasigipgvjs@group.calendar.google.com`)——查看頁面
原始碼才發現。直接下載該行事曆的公開ics feed：
```
https://calendar.google.com/calendar/ical/qpejvbuas1qpq9ugasigipgvjs%40group.calendar.google.com/public/basic.ics
```
不需授權、不用處理該HTML外殼頁面。內容回溯到2009年，共1687筆事件(2026-09-10
實測)。

**當期學期邊界判定**：不用寫死8/1、2/1，而是抓行事曆裡本來就有的
「N學年度第M學期開始」標記事件，取「今天之前最近一次」到「下一次」之間
的區間——因為實測真正開學日每年略有出入。抓不到下一個標記時(理論上不會
發生，資料涵蓋到2027年後)保守用+180天當上限。

**解析方式**：手動regex解析ics(BEGIN:VEVENT區塊切割 + DTSTART/DTEND/
SUMMARY擷取)，沒有加`icalendar`/`ics`套件依賴。原因：實測SUMMARY出現
次數跟BEGIN:VEVENT完全1:1(1687=1687)，代表SUMMARY沒有被RFC5545行折疊
過，不需要處理折疊邏輯；DTEND每筆都存在；SUMMARY目前沒有ICS轉義字元
(`\,` `\;` `\n`)，但仍做了防禦性unescape。符合本專案一貫的低依賴原則。

**輸出格式**：JSON，存`data/thu_academic_calendar.json`，結構：
```json
{
  "semester": "115學年度第1學期",
  "range_start": "2026-08-01",
  "range_end": "2027-02-01",
  "events": [{"date": "...", "end_date": "...或null", "title": "..."}]
}
```
選JSON而非ICS：這裡的用途是「合併進倉庫給程式/人讀」，不是要匯入行事曆
App；JSON結構單純、之後若cot(選課工具)想串接讀取的話最省事。**刻意不放
產生時間戳**——內容不含時間戳，只有事件本身真的變動時git才會偵測到差異，
避免每天固定觸發一次無意義的auto backup commit。

**版控機制**：沒有另外寫git commit邏輯，改把`data/`加進backup.sh既有的
`git add`清單，讓每晚既有的自動commit+push一併帶走。同時發現`jobs/`目錄
(2026-08-30由main.py拆分出來)從建立以來就沒被列進這份清單──11天內
jobs/*.py的異動完全沒被夜間自動備份覆蓋到，這次一併補上(跟本次改動
`jobs/scholarship.py`/`jobs/internship.py`/`jobs/thu_calendar.py`一起
生效)。

**排程**：新增`IntelPusher-ThuCalendar`工作排程(`scripts/
add_thu_calendar_task.ps1`)，每天06:00、pythonw.exe執行
`main.py --source thu_calendar`，比其他既有排程(07:00 daily_recap等)
更早。

## 財經預設bot(TELEGRAM_BOT_TOKEN)停擺調查

**現況**：`.env`裡`TELEGRAM_BOT_TOKEN`目前是空的，`TELEGRAM_CHAT_ID`還
在——自2026-08-10起，`jobs/engine.py`共用管線(fed/etf0050/macro_fred/
twse_tsmc/twse_chunghwa/digest/meta/portfolio等多數來源)的Telegram通知
全數被notify_telegram.py自身的guard靜默跳過(Discord webhook不受影響，
正常推播，所以沒人發現)。

**調查結果**：查`.env.example`的git歷史，程式碼層級從未移除過這個變數
的登記，代表`.env`(未進版控)這個值本身變空不是程式碼決策。時間點跟
finfeed專案除役封存(2026-08-05，見使用者memory)相近——`notify_telegram.py`
docstring明確寫這支bot「借用finfeed既有的bot token」，推測finfeed除役
清理時這支「共用」bot可能被當成finfeed專屬資源處理掉，導致ip失去依賴。
**此為推測，非確認事實**，本repo/本機都沒有decision log記錄這個決定。

**判斷**：不像刻意棄用，應該修復；但修復需要實際的bot token值(只有
BotFather查得到)，AI沒有管道能自己生出來或猜測還原，需使用者親自跟
BotFather核對後提供。
