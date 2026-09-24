/**
 * Google Apps Script: hourly external trigger for .github/workflows/scheduler.yml.
 *
 * Why: GitHub's own `schedule` event has been running 2-6 h late on this account
 * since 2026-08-26, so the hourly tick comes from here via the workflow_dispatch API.
 * Late/duplicate ticks are harmless (scripts/cloud_scheduler.py catches up and runs
 * each task at most once per day).
 *
 * Setup (once): see docs/operations/CLOUD_SCHEDULER.md.
 *   Script Properties -> GITHUB_TOKEN = fine-grained PAT, repo DC-Webhook1 only,
 *   permission "Actions: Read and write" (nothing else).
 *   Then run installTrigger() once from the editor.
 */
const REPO = 'si-kui-a/DC-Webhook1';
const WORKFLOW = 'scheduler.yml';

function tick() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('Script property GITHUB_TOKEN is not set');
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  };
  // Retry transient failures with backoff; a single missed tick is also caught up next hour.
  for (let attempt = 1; attempt <= 3; attempt++) {
    const res = UrlFetchApp.fetch(url, options);
    const code = res.getResponseCode();
    if (code === 204) return;
    if (code < 500 && code !== 429) throw new Error(`dispatch failed: HTTP ${code} ${res.getContentText()}`);
    Utilities.sleep(2000 * Math.pow(2, attempt - 1));
  }
  throw new Error('dispatch failed after 3 attempts');
}

/** Run once: replaces any existing tick triggers with one hourly trigger. */
function installTrigger() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'tick')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('tick').timeBased().everyHours(1).create();
  tick(); // fail fast if the token is wrong
}
