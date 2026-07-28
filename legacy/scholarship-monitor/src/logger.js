// [WHY] 不用 winston/pino 等重量級 logging 框架：本系統單機執行、無多進程需求，
//       純 fs.appendFileSync 已足夠且零額外記憶體/CPU開銷，符合 Low-Compute 原則。
// [HOW] 兩支獨立檔案：activity.log 記錄「正常任務生命週期」，error.log 只記錄「異常」。
//       混在一起會讓 STATUS 掃描時無法快速判斷系統是否健康（AUTO-AUDIT 要求的可稽核性）。
const fs = require('fs');
const path = require('path');

const WORK_DIR = path.join(__dirname, '..', 'work');
const ACTIVITY_LOG = path.join(WORK_DIR, 'activity.log');
const ERROR_LOG = path.join(WORK_DIR, 'error.log');

if (!fs.existsSync(WORK_DIR)) fs.mkdirSync(WORK_DIR, { recursive: true });

function timestamp() {
  return new Date().toISOString();
}

function activity(taskName, detail = {}) {
  const line = `[${timestamp()}] [${taskName}] ${JSON.stringify(detail)}\n`;
  fs.appendFileSync(ACTIVITY_LOG, line);
}

function error(taskName, err, detail = {}) {
  const line = `[${timestamp()}] [${taskName}] ${err.message || err} ${JSON.stringify(detail)}\n${err.stack || ''}\n`;
  fs.appendFileSync(ERROR_LOG, line);
}

module.exports = { activity, error, ACTIVITY_LOG, ERROR_LOG };
