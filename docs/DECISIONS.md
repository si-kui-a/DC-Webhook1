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

**2026-09-25 改為不進版控**：上述機制實際上從未運作——pre-commit guard
擋下任何直接commit到main，backup.sh的`git commit ... || true`把失敗吞掉，
push沒東西可推也回0，log照寫「成功」；行事曆檔就一直以「已暫存、未commit」
留在main上，連帶擋住guard的跨repo同步。查證結果：沒有任何程式讀這個檔
(cot也沒有串接)，GitHub Actions版排程也刻意不推回。因此改列`.gitignore`
、從backup.sh的`git add`清單移除`data/`，並讓backup.sh在commit被拒時如實
寫進backup_errors.log、exit 1。日後若真有程式要讀，再改成由該程式直接抓
Google Calendar，不要恢復每天自動commit。

**排程**：新增`IntelPusher-ThuCalendar`工作排程(`scripts/
add_thu_calendar_task.ps1`)，每天06:00、pythonw.exe執行
`main.py --source thu_calendar`，比其他既有排程(07:00 daily_recap等)
更早。

**在學學生相關性過濾(2026-09-10補充，使用者確認「只要推播與在學學生明確
相關的即可」)**：比照scholarship_util.py/internship_util.py既有的排除詞
慣例，新增`config/thu_calendar_exclude.json`(子字串比對，非regex)，
排除教職員行政會議(教務/校務/系所主管/研究發展委員會議、導師會議、行政
同仁講習)、教職員專屬活動(教職員恢復正常辦公、安全衛生教育訓練)、全校
設施維護通知(停水/停電/設備保養)、校友或公開慶典類(校友、牛奶節、表揚
大會)。63筆→47筆。刻意保守：字面上沒有「學生」兩字但實質是在學學生需
辦理的截止日/考試週/上課起訖/假期/新生入學相關安排，一律保留，不做
未經確認的二次臆測式篩選(例如「新生」相關的接機/入住/講習類事項雖然
針對的是剛入學的新生而非泛稱「在學」學生，但仍屬於學生本人要辦理的
事務，不是行政內部事項，予以保留)。學期起訖標記事件(115學年度第1學期
開始/終了)本身已經反映在semester/range_start/range_end欄位，events
清單裡不再重複列出。

**再收斂(2026-09-10同日補充，使用者確認「非新生、非外籍學生、非休退學」)**：
`config/thu_calendar_exclude.json`再加3類——`new_student_only`(新生)、
`international_student_only`(境外/僑生/外籍)、`leave_or_withdrawal`
(休學/退學，兩個關鍵字缺一不可：「休退學」不包含連續子字串「休學」，
只列一個會漏篩另一種寫法)。47筆→35筆。刻意不動「退選」「停修」相關
事項——課程加退選/停修是學生仍在學狀態下的課程異動，跟「休退學」(學籍
狀態異動)是不同概念，沒有理由一併排除。

**再收斂第二輪(2026-09-10同日補充，使用者確認「非研究生」)**：新增
`graduate_student_only: ["研究生"]`。35筆→30筆，其中4筆是純研究生事項
(論文/學位考試截止日)，符合預期；但「加退選課程開始（大二以上及研究生）」
這筆也被一併排除——它的字面對象是「大二以上『及』研究生」，同時涵蓋
大二以上大學部學生(非研究生族群)，並非純研究生事項，用子字串比對
"研究生"沒辦法分辨「研究生是唯一對象」跟「研究生是多個對象之一」這兩種
情況。已知取捨，未再另外詢問使用者確認，理由：使用者近幾輪指示都是
明確的關鍵字式排除，且此為單一事項、影響範圍小、事後可逆(改設定檔
+重跑即可調整)——已在回覆裡把這個副作用講清楚，使用者若想留下這筆可以
直接說。

**例外清單(2026-09-10同日，使用者確認「這個改回來」)**：加入
`force_include: ["大二以上及研究生"]`，比對優先於所有排除類別。
`_load_filter_config()`同時回傳exclude_keywords跟force_include_keywords，
`is_relevant_to_students()`先檢查force_include再檢查排除詞。30筆→31筆
(「加退選課程開始（大二以上及研究生）」復活，其餘4筆純研究生事項維持
排除)。這是目前唯一的例外項目，不是通用規則——未來若行事曆出現新的
「混合對象」事項(某群體本該排除但同時涵蓋其他該保留的族群)，需要
使用者另外確認才加進這份清單，不會自動套用同樣邏輯。

**移除校友/公開慶典類排除(2026-09-10同日，使用者確認「全校性慶典活動類
可以保留」)**：拿掉`alumni_or_public_events`整個類別(原本的"校友"/
"牛奶節"/"表揚大會")。31筆→34筆，復活「慶祝教師節暨表揚大會」「全球
校友返校日」「東海牛奶節」三筆。目前排除類別只剩：教職員/行政會議、
教職員專屬活動、全校設施維護通知、新生/境外(外籍)學生專屬事項、
休學退學相關截止日、純研究生事項。

**加上Telegram提醒推播(2026-09-10同日，使用者確認「Telegram往後專用於
東海行事曆推播」)**：EDU bot從此不再服務scholarship/internship，改以
這支job為主要用途(sig_watch.py仍照原樣共用同一組token/chat_id，未變動)。
`scrapers/thu_calendar.py`新增
`get_reminder_trigger_dates(event_date, title)`：一般事項提醒節奏是
「前一週+當日」；考試/選課類(關鍵字「考試」「退選」「預選」「停修」
「所選課程」判斷，涵蓋期中/學期考試週、加退選、特殊退選、確認所選
課程、課程預選、停修)節奏是「前一個月+前二週+事件當週的星期一+當日」
(使用者2026-09-10確認)。多天事件(如考試週)一律用起始日當基準算節奏。

`jobs/thu_calendar.py`每天執行時檢查「今天」是否有事項落在任何一個
trigger日期上，有才發一則Telegram訊息(沒有就完全不推播，比照
sig_watch.py的既有慣例，避免通知疲勞)；訊息內容除了今天要提醒的
事項(可能不只一筆，同一天多個事項觸發時全部列在同一則訊息)，還會
附上「下一次推播」的日期跟事項清單(使用者2026-09-10確認「每次推播
都要同時在同一個訊息串寫出下一次推播活動事項與日期」)。「下一次
推播」是指「今天之後最近一次會有訊息發出的日期」，不是行事曆上
最近一筆事項的日期——兩者在觸發密集的考試/選課事項附近可能不同。
2026-09-10實測：當天無觸發事項(正常安靜)，抽樣模擬2026-10-04/
2026-10-20/2026-11-02/2026-11-03四個「期中考試週」的trigger日期
都正確觸發，2026-11-02當天校慶紀念日(當日)+期中考試週(當週一)剛好
同時觸發，兩筆事項正確合併進同一則訊息。

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
