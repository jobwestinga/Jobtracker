// Optional real-browser checks. Run with installed Playwright; no npm/bundler
// dependency is added to this app. All API traffic uses an isolated mock server.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const output = process.env.JOBTRACKER_BROWSER_ARTIFACTS || '/tmp/jobtracker-mobile-check';
fs.mkdirSync(output, { recursive: true });
let head = 1, offline = false, active = null, ops = [], weights = [], delays = {}, holdWeight = null, holdOps = null,
  holdContext = null, failedContext = false, failedWeight = false;
let getWeightCount = 0;
let rejectProfileAuth = false, dropProfileAck = false;
const hits = [];
const subjects = [{ uid: 'subject-a', name: 'Work', color: '#3B82F6', sort_order: 0, is_archived: 0 },
  { uid: 'subject-b', name: 'Reading', color: '#10B981', sort_order: 1, is_archived: 0 },
  { uid: 'subject-malformed', name: '<img src=x onerror="window.injected=true">', color: 'url("https://untrusted.test/collect")', sort_order: 2, is_archived: 0 },
  { uid: 'subject-old', name: 'Archive', color: '#EF4444', sort_order: 0, is_archived: 1 }];
const goals = [{ uid: 'goal-a', name: 'First goal', sort_order: 0, is_focused: 0 },
  { uid: 'goal-b', name: 'Focused second goal', sort_order: 1, is_focused: 1 }];
const sessions = [{ uid: 'session-a', subject_uid: 'subject-a', start_time: '2026-10-10T09:00:00', end_time: '2026-10-10T10:15:00', duration_seconds: 4500 }];
const changes = [];
const sentIds = new Set();
let currentDay = '2026-10-10';
const json = (res, payload, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(payload)); };
function apply(entry) {
  ops.push(entry);
  if (sentIds.has(entry.op_id)) return { ok: true };
  sentIds.add(entry.op_id);
  const p = entry.params;
  let result = { ok: true };
  let change;
  if (entry.op === 'start_subject' || entry.op === 'switch_subject') {
    active = { uid: p.uid, session_uid: p.uid, subject_uid: p.subject_uid, start_time: p.start_time, elapsed_seconds: 0 };
    result = { started: true, session: active };
  } else if (entry.op === 'stop_active_subject') { active = null; result = { stopped: true }; }
  else if (entry.op === 'add_session') {
    const row = { uid: p.uid, subject_uid: p.subject_uid, start_time: p.start_time, end_time: p.end_time,
      duration_seconds: (Date.parse(`${p.end_time}Z`) - Date.parse(`${p.start_time}Z`)) / 1000 };
    sessions.push(row); result = { session: row };
    change = { table: 'sessions', op: 'upsert', uid: row.uid, row: { ...row } };
  } else if (entry.op === 'shift_session') {
    const row = sessions.find((session) => session.uid === p.session_uid);
    const shift = (value) => new Date(Date.parse(`${value}Z`) + p.seconds * 1000).toISOString().slice(0, 19);
    row.start_time = shift(row.start_time); row.end_time = shift(row.end_time);
    result = { session: row };
    change = { table: 'sessions', op: 'upsert', uid: row.uid, row: { ...row } };
  } else if (entry.op === 'update_session') {
    const row = sessions.find((session) => session.uid === p.session_uid);
    Object.assign(row, { subject_uid: p.subject_uid, start_time: p.start_time, end_time: p.end_time,
      duration_seconds: Math.floor((Date.parse(`${p.end_time}Z`) - Date.parse(`${p.start_time}Z`)) / 1000) });
    result = { session: row };
    change = { table: 'sessions', op: 'upsert', uid: row.uid, row: { ...row } };
  }
  else if (entry.op === 'upsert_weight') {
    const existing = weights.find((r) => r.uid === p.weight_uid || r.uid === p.uid) ||
      (!p.weight_uid && weights.find((r) => r.source === 'manual' && r.measured_at.slice(0, 10) === p.measured_at.slice(0, 10)));
    if (p.weight_uid && !existing) { head++; return { skipped: true, reason: 'measurement no longer exists' }; }
    const measuredAt = p.measured_at.length === 10 ? `${p.measured_at}T00:00:00` : p.measured_at;
    const row = { uid: existing?.uid || p.uid, measured_at: measuredAt, weight_kg: p.weight_kg, source: existing?.source || 'manual' };
    weights = weights.filter((r) => r.uid !== row.uid); weights.push(row); weights.sort((a,b)=>a.measured_at.localeCompare(b.measured_at));
    result = { measurement: row };
  } else if (entry.op === 'delete_weight') weights = weights.filter((r) => r.uid !== p.weight_uid);
  else if (entry.op === 'archive_subject' || entry.op === 'unarchive_subject') {
    const row = subjects.find((r) => r.uid === p.subject_uid); row.is_archived = entry.op === 'archive_subject' ? 1 : 0;
    change = { table: 'subjects', op: 'upsert', uid: row.uid, row: { ...row } };
  }
  head++;
  if (change) changes.push({ ...change, seq: head });
  return result;
}
async function chooseWeight(page, whole, decimal) {
  for (let attempt = 0; attempt < 150; attempt++) {
    const button = page.locator(`#weight-whole button[data-value="${whole}"]`);
    if (await button.count()) { await button.click(); break; }
    await entryControls(page, true);
    const low = Number(await page.locator('#weight-whole button').first().getAttribute('data-value'));
    await page.locator(whole < low ? '#weight-lower' : '#weight-higher').click();
  }
  await page.locator(`#weight-decimal button[data-value="${decimal}"]`).click();
}
async function entryControls(page, open) {
  if (await page.locator('#weight-entry-controls').evaluate((details) => details.open) !== open) await page.locator('#weight-entry-controls > summary').click();
}
async function clearWeight(page) {
  await page.locator('#weight-clear').click();
}
async function weightDate(page, value) {
  await entryControls(page, true); await page.locator('#weight-date').fill(value);
}
async function todayWeight(page) {
  await entryControls(page, true); await page.locator('#weight-today').click();
}
async function savedClear(page) {
  await page.waitForFunction(() => document.querySelector('#weight-save').disabled && !document.querySelector('.weight-tile[aria-pressed="true"]') && document.querySelector('#weight-status').textContent.startsWith('Saved'));
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('#weight-edit-actions').isVisible(), false);
}
async function pickerRegressions(browser, url) {
  weights = [
    { uid: 'picker-older', measured_at: '2026-10-08T00:00:00', weight_kg: 78, source: 'manual' },
    { uid: 'picker-tiny', measured_at: '2026-10-09T09:15:22', weight_kg: 0.01, source: 'scale' },
    { uid: 'picker-latest', measured_at: '2026-10-10T00:00:00', weight_kg: 79.55, source: 'manual' },
  ];
  delays['/ops'] = 0;
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Europe/Amsterdam' });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('jobtracker.token', 'picker-regression-token');
    const RealDate = Date;
    let now = new RealDate('2026-10-10T23:59:00').getTime();
    window.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
    window.setPickerClock = (value) => { now = new RealDate(value).getTime(); };
  });
  const refreshWeights = () => page.evaluate(async () => {
    const weight = await import('/weight.js'); weight.invalidateWeight(); weight.renderWeight(document.querySelector('#weight-body'));
  });
  const integers = () => page.locator('#weight-whole button').evaluateAll((buttons) => buttons.map((button) => Number(button.dataset.value)));
  const saveAndWait = async () => {
    const requests = getWeightCount;
    await page.locator('#weight-save').click();
    for (let attempt = 0; getWeightCount === requests && attempt < 300; attempt++) await page.waitForTimeout(10);
    assert.ok(getWeightCount > requests, 'acknowledged save reloads authoritative history');
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  };
  await page.goto(url); await page.waitForSelector('#weight-history .card');
  assert.equal(await page.locator('#view-title').textContent(), 'Weight');
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  assert.deepEqual(await integers(), [77, 78, 79, 80, 81, 82, 83]);
  // Reset is beside Save and usable with entry controls collapsed. It clears
  // partial or complete drafts without deleting anything from saved history.
  assert.equal(await page.locator('#weight-entry-controls').evaluate((node) => node.open), false);
  await page.locator('#weight-whole button[data-value="80"]').click();
  await page.locator('#weight-clear').click();
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  await chooseWeight(page, 80, 5); await page.locator('#weight-clear').click();
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  assert.equal(weights.length, 3);
  // Existing precision survives unchanged edits, including values below .1.
  await page.locator('#weight-history .card').first().click();
  assert.equal(await page.locator('#weight-value').textContent(), '79.55 kg');
  await saveAndWait();
  assert.equal(ops.at(-1).params.weight_kg, 79.55);
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  await page.locator('#weight-history .card').filter({ hasText: 'Scale' }).click();
  assert.equal(await page.locator('#weight-value').textContent(), '0.01 kg');
  assert.equal(await page.locator('#weight-save').isDisabled(), false);
  await saveAndWait();
  assert.equal(ops.at(-1).params.weight_kg, 0.01);
  assert.equal(weights.find((row) => row.uid === 'picker-tiny').measured_at, '2026-10-09T09:15:22');
  await todayWeight(page);
  await chooseWeight(page, 83, 6); await saveAndWait();
  assert.deepEqual(await integers(), [81, 82, 83, 84, 85, 86, 87], 'completed save recenters on the latest reading');
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  assert.equal(await page.locator('#weight-status').textContent(), 'Saved.');
  await clearWeight(page);
  assert.equal(await page.locator('#weight-status').textContent(), '', 'Clear removes an earlier saved receipt');
  // An older-date backfill centers on the chronologically latest row.
  await weightDate(page, '2026-10-07'); await chooseWeight(page, 90, 2);
  await entryControls(page, false);
  assert.match(await page.locator('#weight-entry-day').textContent(), /(\b7\b.*Oct|Oct.*\b7\b).*2026/);
  assert.match(await page.locator('#weight-entry-controls summary').getAttribute('aria-label'), /(\b7\b.*Oct|Oct.*\b7\b).*2026/);
  await saveAndWait();
  assert.deepEqual(await integers(), [81, 82, 83, 84, 85, 86, 87]);
  const backfill = weights.find((row) => row.measured_at.startsWith('2026-10-07'));
  assert.equal(backfill.weight_kg, 90.2);
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-10');
  assert.equal(await page.locator('#weight-entry-day').textContent(), '');
  await page.locator('#weight-history .card').filter({ hasText: '90.2 kg' }).click();
  await page.locator('#weight-date').fill('2026-10-10');
  const beforeCollision = ops.length;
  await page.locator('#weight-save').click();
  assert.match(await page.locator('#weight-status').textContent(), /already has a weighing/);
  assert.equal(ops.length, beforeCollision, 'known date collision never adds an operation');
  await page.locator('#weight-cancel').click();
  await chooseWeight(page, 84, 1); await saveAndWait();
  assert.equal(await page.locator('#weight-status').textContent(), 'Saved.');
  assert.equal(weights.filter((row) => row.source === 'manual' && row.measured_at.startsWith(currentDay)).length, 1);
  assert.equal(weights.find((row) => row.uid === 'picker-latest').weight_kg, 84.1);
  // Polling never shifts the seven choices beneath an active selection.
  await chooseWeight(page, 85, 2);
  assert.equal(await page.locator('#weight-status').textContent(), '', 'a new tile selection clears the previous saved receipt');
  weights.find((row) => row.uid === 'picker-latest').weight_kg = 91.1;
  await refreshWeights();
  await page.waitForFunction(() => document.querySelector('#weight-summary').textContent.includes('91.1'));
  assert.deepEqual(await integers(), [81, 82, 83, 84, 85, 86, 87]);
  assert.equal(await page.locator('#weight-value').textContent(), '85.2 kg');
  await clearWeight(page);
  assert.equal(await page.locator('#weight-status').textContent(), '');
  assert.deepEqual(await integers(), [88, 89, 90, 91, 92, 93, 94]);
  await page.locator('#weight-whole button').first().focus(); await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('End');
  assert.equal(await page.locator('#weight-value').textContent(), '89.9 kg');
  assert.equal(await page.locator('#weight-decimal button[data-value="9"]').evaluate((button) => button === document.activeElement), true);
  await entryControls(page, false);
  await page.evaluate(() => { document.querySelector('#views').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, 'weight-picker-selected-phone.png'), fullPage: true });
  // A denied native request still fills the real viewport, keeps the plot
  // inside its original host, and redraws at the rotated canvas dimensions.
  await page.evaluate(() => {
    window.nativeFullscreenAttempts = 0;
    document.documentElement.requestFullscreen = () => { nativeFullscreenAttempts++; return Promise.reject(new Error('not supported on this phone')); };
    window.originalWeightPlot = document.querySelector('#weight-plot');
  });
  await page.locator('#weight-options-toggle').click();
  assert.equal(await page.locator('#weight-options-toggle').getAttribute('aria-expanded'), 'true');
  assert.equal(await page.locator('#weight-plot-options').isVisible(), true);
  await page.locator('#weight-fullscreen').click(); await page.waitForSelector('#weight-plot.plot-fullscreen');
  assert.equal(await page.locator('#weight-fullscreen').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.evaluate(() => nativeFullscreenAttempts), 1);
  assert.equal(await page.evaluate(() => originalWeightPlot === document.querySelector('#weight-body > #weight-plot')), true);
  for (const selector of ['#weight-range', '#weight-chart-help', '#weight-plot-options', '#weight-options-toggle']) {
    assert.equal(await page.locator(selector).isVisible(), false, `${selector} is hidden in fullscreen`);
  }
  await page.waitForFunction(() => document.querySelector('#weight-canvas').clientHeight > 400);
  const portraitHeight = await page.locator('#weight-canvas').evaluate((canvas) => canvas.clientHeight);
  await page.screenshot({ path: path.join(output, 'weight-fullscreen-portrait.png'), fullPage: true });
  await page.setViewportSize({ width: 844, height: 390 });
  await page.waitForFunction(() => { const canvas = document.querySelector('#weight-canvas'); return canvas.width === Math.round(canvas.clientWidth * Math.min(2, devicePixelRatio)) && canvas.height === Math.round(canvas.clientHeight * Math.min(2, devicePixelRatio)); });
  const landscape = await page.locator('#weight-canvas').evaluate((canvas) => ({ width: canvas.clientWidth, height: canvas.clientHeight }));
  assert.ok(landscape.width > 780 && landscape.height < portraitHeight && landscape.height >= 360, JSON.stringify(landscape));
  await page.screenshot({ path: path.join(output, 'weight-fullscreen-landscape.png'), fullPage: true });
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#weight-plot').evaluate((plot) => plot.classList.contains('plot-fullscreen')), false);
  assert.equal(await page.locator('#weight-fullscreen').getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('#weight-plot-options').isVisible(), true, 'plot options stay open on return');
  assert.equal(await page.locator('#weight-value').textContent(), '89.9 kg', 'fullscreen leaves the entry draft untouched');
  // Entering fullscreen after rotation also expands the chart, and the close
  // control stays at the top right without consuming a toolbar row.
  await page.locator('#weight-range').selectOption('custom');
  const inlineHeight = await page.locator('#weight-canvas').evaluate((canvas) => canvas.clientHeight);
  await page.locator('#weight-fullscreen').click();
  await page.waitForFunction((previous) => document.querySelector('#weight-canvas').clientHeight > previous, inlineHeight);
  assert.equal(await page.locator('#weight-custom').isVisible(), false);
  const close = await page.locator('#weight-fullscreen').boundingBox();
  assert.ok(close.x > 760 && close.y < 20, JSON.stringify(close));
  await page.locator('#weight-fullscreen').click();
  assert.equal(await page.locator('#weight-custom').isVisible(), true);
  await page.locator('#weight-range').selectOption('90');
  await page.locator('#weight-options-toggle').click();
  assert.equal(await page.locator('#weight-plot-options').isVisible(), false);
  await page.evaluate(() => { document.querySelector('#views').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, 'weight-entry-landscape.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: path.join(output, 'weight-picker-laptop.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  assert.equal(await page.locator('.weight-tile').evaluateAll((buttons) => buttons.every((button) => { const box = button.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth; })), true);
  await page.screenshot({ path: path.join(output, 'weight-entry-320.png'), fullPage: true });
  await entryControls(page, true);
  await page.locator('#weight-density').selectOption('compact'); await page.locator('#weight-contrast').selectOption('high');
  await page.screenshot({ path: path.join(output, 'weight-picker-compact-320.png'), fullPage: true });
  await page.reload(); await page.locator('[data-view="weight"]').click(); await page.waitForSelector('#weight-history .card');
  assert.equal(await page.locator('#weight-density').inputValue(), 'compact');
  assert.equal(await page.locator('#weight-contrast').inputValue(), 'high');
  // Midnight is independent of the timer's 03:00 logical day; preserve
  // selections, explicit backfill dates and imported edit dates on resume.
  // An old response issued before midnight cannot reset the automatic date.
  let releaseMidnight;
  holdWeight = new Promise((resolve) => { releaseMidnight = resolve; });
  weights.find((row) => row.uid === 'picker-latest').weight_kg = 92.2;
  const beforeMidnightRequest = getWeightCount;
  await refreshWeights();
  for (let attempt = 0; getWeightCount === beforeMidnightRequest && attempt < 100; attempt++) await page.waitForTimeout(10);
  assert.ok(getWeightCount > beforeMidnightRequest);
  currentDay = '2026-10-11';
  await page.evaluate(async () => { setPickerClock('2026-10-11T00:30:00'); document.dispatchEvent(new Event('visibilitychange')); const weight = await import('/weight.js'); weight.renderWeight(document.querySelector('#weight-body')); });
  await page.waitForFunction(() => document.querySelector('#weight-date').value === '2026-10-11');
  holdWeight = null; releaseMidnight();
  await page.waitForFunction(() => document.querySelector('#weight-summary').textContent.includes('92.2'));
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-11');
  await chooseWeight(page, 91, 3);
  currentDay = '2026-10-12';
  await page.evaluate(() => { setPickerClock('2026-10-12T00:30:00'); document.dispatchEvent(new Event('visibilitychange')); });
  await refreshWeights(); await page.waitForTimeout(30);
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-11');
  assert.equal(await page.locator('#weight-value').textContent(), '91.3 kg');
  await todayWeight(page);
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-12');
  await weightDate(page, '2026-10-01');
  currentDay = '2026-10-13';
  await page.evaluate(() => { setPickerClock('2026-10-13T00:30:00'); document.dispatchEvent(new Event('visibilitychange')); });
  await refreshWeights(); await page.waitForTimeout(30);
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-01');
  await page.locator('#weight-history .card').filter({ hasText: 'Scale' }).click();
  currentDay = '2026-10-14';
  await page.evaluate(() => { setPickerClock('2026-10-14T00:30:00'); document.dispatchEvent(new Event('visibilitychange')); });
  await refreshWeights(); await page.waitForTimeout(30);
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-09');
  assert.equal(await page.locator('#weight-value').textContent(), '0.01 kg');
  // Failed storage never clears the selected entry or editing target.
  await page.locator('#weight-decimal button[data-value="2"]').click();
  const beforeFailedSave = ops.length;
  await page.evaluate(() => {
    window.restoreStorageWrite = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) { if (key === 'jobtracker.outbox') throw new Error('storage full'); return restoreStorageWrite.call(this, key, value); };
  });
  await page.locator('#weight-save').click();
  await page.waitForFunction(() => document.querySelector('#weight-status').classList.contains('error'));
  assert.equal(await page.locator('#weight-value').textContent(), '0.2 kg');
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 2);
  assert.equal(await page.locator('#weight-edit-actions').isVisible(), true);
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-09');
  assert.equal(ops.length, beforeFailedSave);
  await page.evaluate(() => { Storage.prototype.setItem = restoreStorageWrite; });
  // A remotely removed edit is skipped, so keep its selection for a new
  // manual save instead of clearing a weighing that never reached the server.
  weights = weights.filter((row) => row.uid !== 'picker-tiny');
  await saveAndWait();
  assert.match(await page.locator('#weight-status').textContent(), /removed.*selection is kept/);
  assert.equal(await page.locator('#weight-value').textContent(), '0.2 kg');
  assert.equal(await page.locator('#weight-date').inputValue(), '2026-10-09');
  assert.equal(await page.locator('#weight-save').isDisabled(), false);
  assert.equal(await page.locator('#weight-edit-actions').isVisible(), false);
  await saveAndWait();
  assert.equal(ops.at(-1).params.weight_uid, undefined);
  assert.equal(weights.find((row) => row.measured_at.startsWith('2026-10-09')).weight_kg, 0.2);
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  // Closing an account also tears down its fullscreen surface.
  await page.locator('#weight-fullscreen').click(); await page.waitForSelector('#weight-plot.plot-fullscreen');
  // Selection and original precision are private account state.
  weights = [];
  await page.evaluate(async () => { const api = await import('/api.js'); api.setToken('different-picker-account'); const weight = await import('/weight.js'); weight.renderWeight(document.querySelector('#weight-body')); });
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(await page.evaluate(() => document.body.classList.contains('has-plot-fullscreen')), false);
  await page.waitForFunction(() => !document.querySelector('#weight-history .card'));
  // Match the imported history size. Tile taps must not repaint the canvas
  // or rebuild history, even with the original LOESS trend enabled.
  weights = Array.from({ length: 1060 }, (_, index) => ({ uid: `perf-${index}`, measured_at: new Date(Date.UTC(2023, 11, 1 + index)).toISOString().slice(0, 19), weight_kg: 80 + Math.sin(index / 70) * 4, source: 'scale' }));
  await refreshWeights(); await page.waitForSelector('#weight-history .card');
  await page.locator('#weight-range').selectOption('all');
  await page.waitForFunction(() => document.querySelector('#weight-canvas').getAttribute('aria-label').startsWith('1060 weight measurements'));
  const performance = await page.evaluate(async () => {
    const canvas = document.querySelector('#weight-canvas'), ctx = canvas.getContext('2d');
    let fills = 0, historyChanges = 0;
    const originalFill = ctx.fillRect; ctx.fillRect = function(...args) { fills++; return originalFill.apply(this, args); };
    // Let the range change and any resize repaint settle (slow CI runners can
    // land one late), so the counter measures only the tile taps below.
    const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    for (let quiet = 0, seen = -1, guard = 0; quiet < 6 && guard < 300; guard++) {
      await frame(); quiet = fills === seen ? quiet + 1 : 0; seen = fills;
    }
    fills = 0;
    const observer = new MutationObserver((records) => { historyChanges += records.length; });
    observer.observe(document.querySelector('#weight-history'), { childList: true, subtree: true });
    const direct = [], frames = [];
    for (let index = 0; index < 20; index++) {
      const start = performance.now();
      document.querySelectorAll('#weight-whole button')[index % 7].click(); document.querySelectorAll('#weight-decimal button')[index % 10].click();
      direct.push(performance.now() - start);
      await new Promise((resolve) => requestAnimationFrame(() => { frames.push(performance.now() - start); resolve(); }));
    }
    observer.disconnect(); ctx.fillRect = originalFill;
    return { rows: 1060, taps: 40, chartRepaints: fills, historyChanges, directMedianMs: direct.sort((a,b)=>a-b)[10], frameMedianMs: frames.sort((a,b)=>a-b)[10], directMaxMs: Math.max(...direct) };
  });
  assert.equal(performance.chartRepaints, 0); assert.equal(performance.historyChanges, 0);
  assert.ok(performance.directMaxMs < 20, `tile update took ${performance.directMaxMs}ms`);
  await weightZoomChecks(page);
  assert.deepEqual(errors, []);
  await context.close();
  return performance;
}
// Fullscreen pinch/pan/double-tap and keyboard zoom on a dense history. Touches
// are synthetic pointer events, so this checks the gesture maths, not iOS itself.
async function weightZoomChecks(page) {
  const count = async () => Number((await page.locator('#weight-canvas').getAttribute('aria-label')).match(/^(\d+) weight/)?.[1] || 0);
  const firstDay = async () => (await page.locator('#weight-canvas').getAttribute('aria-label')).match(/from (.+?) to /)?.[1];
  const frames = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const touch = (steps) => page.evaluate(async (steps) => {
    const canvas = document.querySelector('#weight-canvas'), box = canvas.getBoundingClientRect();
    for (const [type, pointerId, fx, fy] of steps) {
      canvas.dispatchEvent(new PointerEvent(type, { pointerId, pointerType: 'touch', isPrimary: pointerId === 1, bubbles: true,
        clientX: box.left + box.width * fx, clientY: box.top + box.height * fy }));
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }
  }, steps);
  await page.locator('#weight-range').selectOption('90');
  await page.locator('#weight-fullscreen').click(); await page.waitForSelector('#weight-plot.plot-fullscreen');
  await frames();
  const initial = await count();
  assert.ok(initial > 30, `90-day view shows ${initial} weighings`);
  assert.equal(await page.locator('#weight-zoom-reset').isVisible(), false);
  assert.equal(await page.locator('#weight-canvas').evaluate((canvas) => getComputedStyle(canvas).touchAction), 'none');
  await page.locator('#weight-canvas').focus(); await page.keyboard.press('+'); await frames();
  assert.ok(await count() < initial, 'keyboard zoom narrows the view');
  assert.equal(await page.locator('#weight-zoom-reset').isVisible(), true);
  await page.keyboard.press('0'); await frames();
  assert.equal(await count(), initial);
  // Two fingers spreading apart around the middle zoom in.
  await touch([['pointerdown', 1, 0.45, 0.5], ['pointerdown', 2, 0.55, 0.5], ['pointermove', 1, 0.3, 0.5], ['pointermove', 2, 0.7, 0.5],
    ['pointermove', 1, 0.15, 0.5], ['pointermove', 2, 0.85, 0.5], ['pointerup', 1, 0.15, 0.5], ['pointerup', 2, 0.85, 0.5]]);
  await frames();
  const zoomed = await count();
  assert.ok(zoomed > 0 && zoomed < initial / 2, `pinch shows ${zoomed} of ${initial} weighings`);
  // One finger dragging right moves to earlier dates, beyond the 90-day range.
  const before = await firstDay();
  await touch([['pointerdown', 1, 0.2, 0.5], ['pointermove', 1, 0.5, 0.5], ['pointermove', 1, 0.95, 0.5], ['pointerup', 1, 0.95, 0.5]]);
  await frames();
  assert.notEqual(await firstDay(), before, 'pan moves the visible dates');
  // A tap inspects the nearest weighing; a double tap restores the full view.
  await touch([['pointerdown', 1, 0.5, 0.5], ['pointerup', 1, 0.5, 0.5]]);
  assert.match(await page.locator('#weight-chart-detail').textContent(), / kg$/);
  assert.equal(await page.locator('#weight-chart-detail').isVisible(), true, 'the inspected weighing shows in fullscreen');
  await page.waitForTimeout(400);
  await touch([['pointerdown', 1, 0.5, 0.5], ['pointerup', 1, 0.5, 0.5], ['pointerdown', 1, 0.5, 0.5], ['pointerup', 1, 0.5, 0.5]]);
  await frames();
  assert.equal(await count(), initial, 'double tap resets the zoom');
  await touch([['pointerdown', 1, 0.45, 0.5], ['pointerdown', 2, 0.55, 0.5], ['pointermove', 1, 0.2, 0.5], ['pointermove', 2, 0.8, 0.5], ['pointerup', 1, 0.2, 0.5], ['pointerup', 2, 0.8, 0.5]]);
  await frames();
  assert.ok(await count() < initial);
  await page.keyboard.press('Escape'); await frames();
  assert.equal(await count(), initial, 'leaving fullscreen restores the chosen range');
  await page.locator('#weight-range').selectOption('all');
}

async function coldWeightFailure(browser, url) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('jobtracker.token', 'cold-weight-isolated-account'));
  weights = [{ uid: 'cold-cached', measured_at: '2026-10-10T09:00:00', weight_kg: 85.4, source: 'scale' }];
  failedContext = true;
  let release;
  holdContext = new Promise((resolve) => { release = resolve; });
  await page.goto(url); await page.waitForSelector('#weight-history .card');
  assert.equal(await page.locator('#view-title').textContent(), 'Weight');
  await chooseWeight(page, 85, 6);
  holdContext = null; release();
  await page.waitForFunction(() => document.querySelector('#banner').textContent.includes('Problem'));
  assert.equal(await page.locator('#weight-value').textContent(), '85.6 kg', 'tracking-context failure cannot erase a weight draft');
  assert.equal(await page.locator('#setup').isVisible(), false);
  await page.evaluate(() => localStorage.removeItem('jobtracker.mirror'));
  failedWeight = true;
  await page.reload(); await page.waitForSelector('#weight-history .card');
  assert.match(await page.locator('#weight-history').textContent(), /85\.4 kg/);
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  await chooseWeight(page, 85, 7);
  await page.waitForFunction(() => document.querySelector('#banner').textContent.includes('Problem'));
  assert.equal(await page.locator('#weight-value').textContent(), '85.7 kg', 'private cached weight works without a tracking mirror or either GET');
  failedContext = false; failedWeight = false;
  await context.close();
}
async function offlineShellCheck(browser, url) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('jobtracker.token', 'offline-shell-contract-account'));
  await page.goto(url);
  try { await page.waitForSelector('#weight-history .card', { timeout: 15000 }); }
  catch (error) { console.error('offline-shell initial state', { errors, requests: hits.slice(-15), body: await page.locator('body').innerText() }); throw error; }
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  const precache = await page.evaluate(async () => {
    const keys = await caches.keys();
    const cache = await caches.open(keys.find((name) => name.startsWith('jobtracker-')));
    const resources = (await cache.keys()).map((request) => new URL(request.url).pathname);
    return { resources, privateCached: Boolean(await cache.match('/api/weight')) };
  });
  assert.ok(precache.resources.includes('/features/weight/index.js'));
  assert.ok(precache.resources.includes('/data/outbox.js'));
  assert.ok(precache.resources.includes('/shared/fullscreen.js'));
  assert.ok(precache.resources.includes('/features/weight/styles.css'));
  assert.equal(precache.privateCached, false);
  await context.setOffline(true);
  await page.reload(); await page.waitForSelector('#weight-history .card');
  assert.equal(await page.locator('#view-title').textContent(), 'Weight');
  assert.equal(await page.locator('#weight-whole button').count(), 7);
  assert.equal(await page.locator('#weight-decimal button').count(), 10);
  assert.ok(await page.locator('#weight-whole button').first().evaluate((button) => button.getBoundingClientRect().width) > 45,
    'feature CSS must load from the offline shell');
  const whole = Number(await page.locator('#weight-whole button').nth(3).getAttribute('data-value'));
  await chooseWeight(page, whole, 3); await page.locator('#weight-save').click();
  await page.waitForFunction(() => document.querySelector('#weight-status').textContent.startsWith('Saved on this phone'));
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  const queued = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox')));
  assert.equal(queued.length, 1); assert.equal(queued[0].op, 'upsert_weight');
  await page.reload(); await page.waitForSelector('#weight-history .card');
  assert.match(await page.locator('#weight-history .card').first().textContent(), new RegExp(`${whole}\\.3 kg`));
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))), queued);
  await page.locator('[data-view="today"]').click(); await page.waitForSelector('#subject-list .card');
  await page.locator('[data-view="graphs"]').click();
  await page.waitForFunction(() => document.querySelector('#graph-body').textContent.includes('Could not load graphs'));
  await page.locator('[data-view="weight"]').click();
  await context.setOffline(false); await page.locator('#refresh').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  assert.deepEqual(errors, []);
  await context.close();
}
async function sessionEntryChecks(browser, url) {
  const fractional = { uid: 'session-fractional', subject_uid: 'subject-b', start_time: '2026-10-09T07:00:00.900000',
    end_time: '2026-10-09T08:30:00.100000', duration_seconds: 5399 };
  sessions.push(fractional);
  const recoveredShort = { uid: 'session-short', subject_uid: 'subject-b', start_time: '2026-10-08T06:00:00.900000',
    end_time: '2026-10-08T06:00:30.899999', duration_seconds: 29 };
  sessions.push(recoveredShort);
  const context = await browser.newContext({ viewport: { width: 320, height: 740 }, isMobile: true, hasTouch: true, timezoneId: 'Europe/Amsterdam' });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript((day) => {
    localStorage.setItem('jobtracker.token', 'isolated-session-entry-account');
    const RealDate = Date;
    let now = new RealDate(`${day}T09:30:00`).getTime();
    window.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
    window.setSessionClock = (value) => { now = new RealDate(value).getTime(); };
  }, currentDay);
  await page.goto(url); await page.waitForFunction(() => localStorage.getItem('jobtracker.mirror'));
  await page.locator('[data-view="sessions"]').click(); await page.waitForSelector('#session-list .card');
  const openAdd = async () => { await page.locator('#add-session').click(); await page.waitForSelector('#sheet:not(.hidden)'); };
  const openCustom = async () => { await openAdd(); await page.locator('.session-custom > summary').click(); };
  const choose170 = async () => {
    for (const minutes of [120, 30, 15, 5]) await page.locator(`.session-duration-chunks [data-minutes="${minutes}"]`).click();
  };
  const countOps = (name) => ops.filter((op) => op.op === name).length;
  const emptyQueue = () => page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  const acknowledged = async (name, previous) => {
    for (let attempt = 0; countOps(name) === previous && attempt < 300; attempt++) await page.waitForTimeout(10);
    assert.ok(countOps(name) > previous, `${name} reaches the server`); await emptyQueue();
  };
  const minuteDifference = (op) => (Date.parse(`${op.params.end_time}Z`) - Date.parse(`${op.params.start_time}Z`)) / 60000;

  // A closed sheet cannot create another operation through a second queued
  // click, including a click on a different mutation control in that sheet.
  await openAdd();
  const before90 = countOps('add_session');
  delays['/ops'] = 150;
  await page.evaluate(() => {
    const preset = document.querySelector('.session-quick-presets [data-minutes="90"]');
    preset.click(); preset.click();
    [...document.querySelectorAll('#sheet button')].find((button) => button.textContent === 'Add with these times').click();
  });
  await page.waitForFunction(() => document.querySelector('#sheet').classList.contains('hidden'));
  await acknowledged('add_session', before90);
  assert.equal(countOps('add_session'), before90 + 1);
  assert.equal(minuteDifference(ops.filter((op) => op.op === 'add_session').at(-1)), 90);
  delays['/ops'] = 0;

  await openCustom();
  assert.equal(await page.locator('.session-duration-chunks button').count(), 7);
  assert.equal(await page.getByRole('button', { name: 'Add, ending now', exact: true }).isDisabled(), true);
  const beforeSelection = ops.length;
  await choose170();
  await page.locator('.session-duration-chunks [data-minutes="240"]').focus();
  await page.locator('.session-duration-chunks [data-minutes="240"]').press('Space');
  assert.equal(await page.locator('#session-custom-total').textContent(), '6h 50m · 410 min');
  await page.locator('.session-duration-chunks [data-minutes="240"]').press('Space');
  assert.equal(await page.locator('#session-custom-total').textContent(), '2h 50m · 170 min');
  assert.equal(await page.locator('#session-custom-total').textContent(), '2h 50m · 170 min');
  assert.equal(await page.locator('.session-duration-chunks [aria-pressed="true"]').count(), 4);
  await page.locator('.session-duration-chunks [data-minutes="120"]').click();
  assert.equal(await page.locator('#session-custom-total').textContent(), '50m · 50 min');
  await page.locator('.session-duration-chunks [data-minutes="120"]').click();
  assert.equal(await page.locator('#session-custom-total').textContent(), '2h 50m · 170 min');
  assert.equal(ops.length, beforeSelection, 'selection does not persist before confirmation');
  await page.getByRole('button', { name: 'Clear', exact: true }).click();
  assert.equal(await page.locator('.session-duration-chunks [aria-pressed="true"]').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Add, ending now', exact: true }).isDisabled(), true);
  await choose170();
  assert.equal(await page.locator('.session-duration-chunks button').evaluateAll((buttons) => buttons.every((button) => {
    const box = button.getBoundingClientRect(); return box.width >= 44 && box.height >= 44 && box.left >= 0 && box.right <= innerWidth;
  })), true, 'all chunk controls fit320px with usable touch targets');
  await page.screenshot({ path: path.join(output, 'session-custom-320.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, 'session-custom-phone.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  await page.getByLabel('Subject', { exact: true }).selectOption('subject-b');
  await page.evaluate((day) => setSessionClock(`${day}T09:35:27`), currentDay);

  // Failed local storage keeps the exact draft for retry. A durable queued
  // save closes the sheet permanently, even though send reports ok:false.
  await page.evaluate(() => {
    window.restoreSessionStorageWrite = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === 'jobtracker.outbox') throw new Error('storage full');
      return restoreSessionStorageWrite.call(this, key, value);
    };
  });
  await page.getByRole('button', { name: 'Add, ending now', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('#sheet').classList.contains('hidden') && document.querySelector('#banner').textContent.includes('cannot save'));
  assert.equal(await page.locator('#session-custom-total').textContent(), '2h 50m · 170 min');
  assert.equal(await page.locator('.session-duration-chunks [aria-pressed="true"]').count(), 4);
  assert.equal(await page.getByLabel('Subject', { exact: true }).inputValue(), 'subject-b');
  assert.equal(ops.length, beforeSelection);
  await page.evaluate(() => { Storage.prototype.setItem = restoreSessionStorageWrite; });
  offline = true; await context.setOffline(true);
  await page.evaluate(() => { const button = document.querySelector('.session-custom-actions .primary'); button.click(); button.click(); });
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 1 && document.querySelector('#banner').textContent.startsWith('Saved on this phone'));
  assert.equal(await page.locator('#sheet').isVisible(), false, 'durable queued save cannot reopen for a retry');
  const queued = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[0]);
  assert.equal(queued.op, 'add_session'); assert.equal(queued.params.subject_uid, 'subject-b');
  assert.equal(minuteDifference(queued), 170); assert.equal(queued.params.end_time, `${currentDay}T09:35:27`);
  assert.ok(queued.params.uid);
  await page.evaluate(() => document.querySelector('.session-custom-actions .primary').click());
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox')).length), 1);
  const beforeReconnect = countOps('add_session');
  offline = false; await context.setOffline(false); await page.locator('#refresh').click();
  await acknowledged('add_session', beforeReconnect);
  assert.equal(ops.filter((op) => op.op_id === queued.op_id).length, 1);
  assert.equal(sessions.find((row) => row.uid === queued.params.uid).duration_seconds, 170 * 60);

  // Other days retain precise datetime entry and omit ending-now controls.
  await page.locator('#day-date').fill('2026-10-08');
  await page.waitForFunction(() => document.querySelector('#session-list .name')?.textContent === 'Day 2026-10-08');
  await openAdd();
  assert.equal(await page.locator('.session-quick-presets').count(), 0);
  assert.equal(await page.locator('.session-custom').count(), 0);
  await page.getByLabel('Start', { exact: true }).fill('2026-10-08T23:40');
  await page.getByLabel('End', { exact: true }).fill('2026-10-09T02:30');
  const beforePrecise = countOps('add_session');
  await page.getByRole('button', { name: 'Add with these times', exact: true }).click();
  await acknowledged('add_session', beforePrecise);
  const precise = ops.filter((op) => op.op === 'add_session').at(-1);
  assert.equal(precise.params.start_time, '2026-10-08T23:40:00');
  assert.equal(precise.params.end_time, '2026-10-09T02:30:00');
  await page.locator('#day-today').click();

  // A server commit followed by failed acknowledgement storage retains the
  // original durable entry. Retrying must not create a fresh session identity.
  await openCustom(); await choose170();
  const beforeAck = countOps('add_session'), rowsBeforeAck = sessions.length;
  await page.evaluate(() => {
    window.restoreAckStorageWrite = Storage.prototype.setItem;
    let writes = 0;
    Storage.prototype.setItem = function(key, value) {
      if (key === 'jobtracker.outbox' && ++writes === 2) throw new Error('ack storage full');
      return restoreAckStorageWrite.call(this, key, value);
    };
  });
  await page.evaluate(() => { const confirm = document.querySelector('.session-custom-actions .primary'); confirm.click(); confirm.click(); });
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 1 && document.querySelector('#banner').textContent.startsWith('Saved on this phone'));
  assert.equal(countOps('add_session'), beforeAck + 1);
  assert.equal(await page.locator('#sheet').isVisible(), false);
  const retainedAck = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[0]);
  await page.evaluate(() => { document.querySelector('.session-custom-actions .primary').click(); Storage.prototype.setItem = restoreAckStorageWrite; });
  await page.locator('#refresh').click(); await acknowledged('add_session', beforeAck + 1);
  assert.equal(ops.filter((op) => op.op_id === retainedAck.op_id).length, 2, 'retry transmits the original receipt ID');
  assert.equal(sessions.length, rowsBeforeAck + 1, 'retry cannot insert a second session');

  const openFractional = async (subject) => {
    await page.locator('[data-view="today"]').click();
    await page.getByRole('button', { name: `Edit ${subject}`, exact: true }).click();
    await page.getByRole('button', { name: 'Session history', exact: true }).click();
    await page.locator('#sheet-body button').filter({ hasText: '07:00–08:30' }).click();
  };
  await openFractional('Reading');
  const beforeFractional = countOps('update_session');
  await page.getByLabel('Subject', { exact: true }).selectOption('subject-a');
  await page.getByRole('button', { name: 'Save', exact: true }).click(); await acknowledged('update_session', beforeFractional);
  const unchanged = ops.filter((op) => op.op === 'update_session').at(-1);
  assert.equal(unchanged.params.session_uid, fractional.uid);
  assert.equal(unchanged.params.start_time, '2026-10-09T07:00:00.900000');
  assert.equal(unchanged.params.end_time, '2026-10-09T08:30:00.100000');
  assert.equal(fractional.duration_seconds, 5399, 'subject-only edit preserves fractional duration');
  await openFractional('Work');
  await page.getByLabel('Start', { exact: true }).fill('2026-10-09T07:05');
  const beforeEndpoint = countOps('update_session');
  await page.getByRole('button', { name: 'Save', exact: true }).click(); await acknowledged('update_session', beforeEndpoint);
  const edited = ops.filter((op) => op.op === 'update_session').at(-1);
  assert.equal(edited.params.start_time, '2026-10-09T07:05:00');
  assert.equal(edited.params.end_time, '2026-10-09T08:30:00.100000', 'editing one endpoint preserves the other exactly');

  await page.locator('[data-view="today"]').click();
  await page.getByRole('button', { name: 'Edit Work', exact: true }).click();
  await page.getByRole('button', { name: 'Session history', exact: true }).click();
  await page.locator('#sheet-body button').filter({ hasText: '07:05–08:30' }).click();
  await page.getByLabel('End', { exact: true }).evaluate((input) => { input.value = '2026-10-09T07:05:20'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  const beforeTooShort = countOps('update_session');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  assert.equal(await page.locator('#sheet').isVisible(), true);
  assert.equal(await page.locator('#banner').textContent(), 'A session must last at least 30 seconds.');
  assert.equal(countOps('update_session'), beforeTooShort);
  assert.equal(await page.getByLabel('End', { exact: true }).inputValue(), '2026-10-09T07:05:20');
  await page.getByLabel('End', { exact: true }).evaluate((input) => { input.value = '2026-10-09T07:05:30'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.getByRole('button', { name: 'Save', exact: true }).click(); await acknowledged('update_session', beforeTooShort);
  assert.equal(fractional.uid, 'session-fractional'); assert.equal(fractional.duration_seconds, 30);
  await page.locator('[data-view="sessions"]').click(); await openAdd();
  await page.getByLabel('Start', { exact: true }).fill(`${currentDay}T09:00`);
  await page.getByLabel('End', { exact: true }).fill(`${currentDay}T09:00`);
  const beforeZero = countOps('add_session');
  await page.getByRole('button', { name: 'Add with these times', exact: true }).click();
  assert.equal(await page.locator('#sheet').isVisible(), true);
  assert.equal(await page.locator('#banner').textContent(), 'A session must last at least 30 seconds.');
  assert.equal(countOps('add_session'), beforeZero);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length), 0);
  await page.locator('#sheet-close').click();

  await page.locator('[data-view="today"]').click();
  await page.getByRole('button', { name: 'Edit Reading', exact: true }).click();
  await page.getByRole('button', { name: 'Session history', exact: true }).click();
  await page.locator('#sheet-body button').filter({ hasText: '06:00–06:00' }).click();
  const beforeShortMove = ops.length;
  await page.getByRole('button', { name: 'Move session 1 day earlier', exact: true }).click();
  assert.equal(await page.locator('#banner').textContent(), 'A session must last at least 30 seconds.');
  await page.getByRole('button', { name: 'Duplicate to today', exact: true }).click();
  assert.equal(await page.locator('#sheet').isVisible(), true);
  assert.equal(ops.length, beforeShortMove, 'a short recovered row cannot jam the queue with a refused shift/copy');
  assert.equal(sessions.find((row) => row.uid === recoveredShort.uid), recoveredShort, 'recovered row remains intact');
  await page.getByLabel('End', { exact: true }).evaluate((input) => { input.value = '2026-10-08T06:00:31'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  const beforeCorrecting = countOps('update_session');
  await page.getByRole('button', { name: 'Save', exact: true }).click(); await acknowledged('update_session', beforeCorrecting);
  assert.equal(recoveredShort.uid, 'session-short'); assert.equal(recoveredShort.duration_seconds, 30);

  // Subject history, Sessions and agenda all use the same exact-UID editor.
  const openWorkHistory = async () => {
    await page.locator('[data-view="today"]').click();
    await page.getByRole('button', { name: 'Edit Work', exact: true }).click();
    await page.getByRole('button', { name: 'Session history', exact: true }).click();
    await page.locator('#sheet-body button').filter({ hasText: '09:00–10:15' }).click();
  };
  const original = { ...sessions.find((row) => row.uid === 'session-a') };
  await openWorkHistory();
  assert.equal(Date.parse(`${await page.getByLabel('Start', { exact: true }).inputValue()}Z`), Date.parse(`${original.start_time}Z`));
  assert.equal(await page.locator('.session-nudges button').count(), 6);
  assert.equal(await page.locator('.session-nudges button').evaluateAll((buttons) => buttons.every((button) => {
    const box = button.getBoundingClientRect(); return box.width >= 44 && box.height >= 44 && box.left >= 0 && box.right <= innerWidth;
  })), true);
  await page.screenshot({ path: path.join(output, 'session-edit-320.png'), fullPage: true });
  const beforeShift = countOps('shift_session');
  await page.evaluate(() => { const button = document.querySelector('.session-nudges [data-seconds="-86400"]'); button.click(); button.click(); });
  await acknowledged('shift_session', beforeShift);
  const earlier = ops.filter((op) => op.op === 'shift_session').at(-1);
  assert.equal(countOps('shift_session'), beforeShift + 1);
  assert.deepEqual(earlier.params, { session_uid: original.uid, seconds: -86400 });
  const moved = sessions.find((row) => row.uid === original.uid);
  assert.equal(Date.parse(`${original.start_time}Z`) - Date.parse(`${moved.start_time}Z`), 86400000);
  assert.equal(Date.parse(`${original.end_time}Z`) - Date.parse(`${moved.end_time}Z`), 86400000);
  assert.equal(moved.duration_seconds, original.duration_seconds);
  await openWorkHistory();
  const beforeLater = countOps('shift_session');
  await page.getByRole('button', { name: 'Move session 1 day later', exact: true }).click();
  await acknowledged('shift_session', beforeLater);
  assert.deepEqual(ops.filter((op) => op.op === 'shift_session').at(-1).params, { session_uid: original.uid, seconds: 86400 });
  assert.equal(moved.start_time, original.start_time); assert.equal(moved.end_time, original.end_time);
  assert.equal(moved.duration_seconds, original.duration_seconds);
  await page.locator('[data-view="sessions"]').click(); await page.locator('#session-list .card').first().click();
  assert.equal(await page.locator('.session-nudges [data-seconds="86400"]').count(), 1);
  await page.locator('#sheet-close').click();
  await page.locator('[data-view="graphs"]').click(); await page.waitForSelector('.bars');
  await page.locator('[data-mode="agenda"]').click(); await page.waitForSelector('.agenda-block');
  await page.locator('.agenda-block').click();
  assert.equal(await page.locator('.session-nudges [data-seconds="-86400"]').count(), 1);
  await page.locator('#sheet-close').click();
  assert.deepEqual(errors, []);
  await context.close();
}
async function syncStatusChecks(browser, url) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript((day) => {
    localStorage.setItem('jobtracker.token', 'isolated-sync-status-token');
    const RealDate = Date; let now = new RealDate(`${day}T09:30:00`).getTime();
    window.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
    window.setSyncClock = (value) => { now = new RealDate(value).getTime(); };
  }, currentDay);
  await page.goto(url);
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent.startsWith('Synced '));
  const confirmed = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.sync-status')).lastSuccessAt);
  const originalActive = JSON.stringify(active);
  const openQuick = async () => {
    await page.locator('[data-view="today"]').click();
    await page.getByRole('button', { name: 'Quick add time to Work', exact: true }).click();
  };

  offline = true;
  await page.locator('#refresh').click();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent === 'Offline');
  await openQuick();
  await page.locator('.session-quick-presets [data-minutes="15"]').click();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent === 'Offline · 1 waiting');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.focusKey), 'subject:subject-a:quick-add',
    'a queued save keeps keyboard focus on its subject control after repaint');
  const queued = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[0]);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.sync-status')).lastSuccessAt), confirmed);
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent === 'Offline · 1 waiting');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[0].op_id), queued.op_id);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.sync-status')).lastSuccessAt), confirmed);
  offline = false;
  await page.evaluate((day) => setSyncClock(`${day}T09:40:00`), currentDay);
  await page.locator('#refresh').click();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent.startsWith('Synced ') &&
    JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  assert.ok(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.sync-status')).lastSuccessAt) > confirmed);
  assert.equal(ops.filter((entry) => entry.op_id === queued.op_id).length, 1);
  assert.equal(JSON.stringify(active), originalActive, 'manual Quick Add leaves the live timer untouched');

  // A server commit with failed local receipt storage stays saved, visibly
  // waiting, with the same UID until replay can remove its original operation.
  await openQuick();
  await page.evaluate(() => {
    let writes = 0;
    window.restoreStatusStorage = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === 'jobtracker.outbox' && ++writes === 2) throw Error('ack storage full');
      return restoreStatusStorage.call(this, key, value);
    };
  });
  await page.locator('.session-quick-presets [data-minutes="30"]').click();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent === 'Sync attention · 1 waiting');
  const retained = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[0]);
  assert.equal(ops.filter((entry) => entry.op_id === retained.op_id).length, 1);
  // The status line itself opens the details; a title tooltip is unreachable on a phone.
  await page.locator('#sync-status').click();
  assert.equal(await page.locator('#sheet-title').textContent(), 'Settings');
  assert.match(await page.locator('#sheet-body').textContent(), /Last successful sync/);
  await page.evaluate(() => { Storage.prototype.setItem = restoreStatusStorage; });
  await page.getByRole('button', { name: 'Send anything waiting', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent.startsWith('Synced ') &&
    JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  assert.equal(ops.filter((entry) => entry.op_id === retained.op_id).length, 2);
  assert.equal(sessions.filter((row) => row.uid === retained.params.uid).length, 1);
  assert.equal(JSON.stringify(active), originalActive);
  assert.deepEqual(errors, []);
  await context.close();
}

async function iconChecks(browser, url) {
  const context = await browser.newContext({ viewport: { width: 1200, height: 1000 } });
  const page = await context.newPage();
  const writes = [];
  page.on('request', (request) => { if (request.method() !== 'GET') writes.push(request.method()); });
  await page.goto(`${url}/phone/`);
  assert.equal(await page.locator('article').count(), 2);
  assert.deepEqual(await page.locator('article img').evaluateAll((images) => images.map((image) => new URL(image.src).pathname)),
    ['/icons/weight.png', '/icons/cutout.png']);
  assert.equal(await page.locator('article img').evaluateAll((images) => images.every((image) => image.complete && image.naturalWidth === 512)), true);
  await page.screenshot({ path: path.join(output, 'install-guide.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  for (const [route, icon] of [['/phone/weight/', 'weight'], ['/phone/jobtracker/', 'cutout']]) {
    await page.goto(url + route);
    assert.equal(await page.locator('link[rel="apple-touch-icon"]').getAttribute('href'), `/icons/${icon}-touch.png`);
    const manifest = await page.evaluate(async () => (await fetch(document.querySelector('link[rel="manifest"]').href)).json());
    assert.equal(manifest.icons[0].src, `/icons/${icon}.png`);
  }
  await page.goto(url);
  assert.equal(await page.locator('link[rel="apple-touch-icon"]').getAttribute('href'), '/icons/weight-touch.png');
  const defaultManifest = await page.evaluate(async () => (await fetch(document.querySelector('link[rel="manifest"]').href)).json());
  assert.equal(defaultManifest.icons[0].src, '/icons/weight.png');
  assert.deepEqual(writes, [], 'reading installation pages never writes application data');
  await context.close();
}

async function accountControlsChecks(browser, url) {
  const context = await browser.newContext({ viewport: { width: 320, height: 740 }, isMobile: true, hasTouch: true });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('account-initialized')) {
      localStorage.setItem('jobtracker.token', 'account-controls-token');
      sessionStorage.setItem('account-initialized', 'yes');
    }
  });
  await page.route('**/api/context', (route) => route.fulfill({ json: { today: currentDay, day_start: '03:30', head, active: null } }));
  await page.goto(url);
  await page.waitForFunction(() => localStorage.getItem('jobtracker.mirror'));
  await page.locator('#open-settings').click();
  const iconLink = page.getByRole('link', { name: 'Home Screen installation guide' });
  assert.equal(await iconLink.getAttribute('href'), '/phone/');
  assert.equal(await iconLink.getAttribute('target'), '_blank');
  assert.equal(await page.getByLabel('Day starts at', { exact: true }).inputValue(), '03:30');
  const beforeSettings = ops.filter((entry) => entry.op === 'set_setting').length;
  await page.getByLabel('Day starts at', { exact: true }).fill('04:45');
  const settingAcknowledged = page.waitForResponse((response) => response.url().endsWith('/ops') &&
    response.request().method() === 'POST' && JSON.parse(response.request().postData()).ops.some((entry) => entry.op === 'set_setting'));
  await page.evaluate(() => {
    const button = document.querySelector('#settings-day-save'); button.click(); button.click();
  });
  await settingAcknowledged;
  await page.waitForFunction(() => document.querySelector('#sheet').classList.contains('hidden') &&
    JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  assert.equal(ops.filter((entry) => entry.op === 'set_setting').length, beforeSettings + 1);
  assert.deepEqual(ops.filter((entry) => entry.op === 'set_setting').at(-1).params,
    { key: 'day_start_time', value: '04:45' });

  await page.locator('#open-settings').click();
  const cached = await page.evaluate(() => localStorage.getItem('jobtracker.mirror'));
  await page.evaluate(() => {
    window.restoreAccountRemove = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function(key) {
      if (key === 'jobtracker.token') throw new Error('storage blocked');
      return restoreAccountRemove.call(this, key);
    };
  });
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Disconnect this phone', exact: true }).click();
  assert.match(await page.locator('#banner').textContent(), /still connected/);
  assert.equal(await page.evaluate(() => localStorage.getItem('jobtracker.token')), 'account-controls-token');
  assert.equal(await page.evaluate(() => localStorage.getItem('jobtracker.mirror')), cached);
  assert.equal(await page.locator('#sheet').isVisible(), true);
  await page.evaluate(() => { Storage.prototype.removeItem = restoreAccountRemove; });

  // A rejected current token must not route its durable work to a new token.
  await page.route('**/ops', (route) => route.fulfill({ status: 503, json: { detail: 'offline for this check' } }));
  await page.route('**/api/context', (route) => route.fulfill({ status: 401, json: { detail: 'token rejected' } }));
  await page.evaluate(() => localStorage.setItem('jobtracker.outbox', JSON.stringify([
    { op_id: 'account-pending', op: 'add_goal', params: { uid: 'pending-goal', name: 'Saved here' } },
  ])));
  await page.locator('#sheet-close').click();
  await page.locator('#refresh').click();
  await page.waitForSelector('#setup:not(.hidden)');
  const queue = await page.evaluate(() => localStorage.getItem('jobtracker.outbox'));
  await page.locator('#setup-token').fill('different-account-token');
  await page.locator('#setup-save').click();
  await page.waitForFunction(() => document.querySelector('#setup-error').textContent.includes('original token'));
  assert.equal(await page.evaluate(() => localStorage.getItem('jobtracker.token')), 'account-controls-token');
  assert.equal(await page.evaluate(() => localStorage.getItem('jobtracker.outbox')), queue);
  const otherTab = await context.newPage();
  await otherTab.goto(`${url}/api.js`);
  await otherTab.evaluate(() => localStorage.setItem('jobtracker.token', 'different-account-token'));
  await page.waitForFunction(() => document.querySelector('#setup-error').textContent.includes('Reconnect the original token'));
  assert.equal(await page.locator('#app').isVisible(), false, 'another tab cannot leave the original private view open');
  assert.equal(await page.evaluate(async () => (await import('/api.js')).pendingForProjection().length), 0);
  await page.reload();
  await page.waitForSelector('#setup:not(.hidden)');
  assert.equal(await page.locator('#app').isVisible(), false, 'cold startup also requires the original queue owner');
  await page.locator('#setup-token').fill('different-account-token');
  await page.locator('#setup-save').click();
  await page.waitForFunction(() => document.querySelector('#setup-error').textContent.includes('original token'));
  assert.equal(await page.locator('#app').isVisible(), false, 'resubmitting the current wrong token cannot leave recovery');
  await page.locator('#setup-token').fill('account-controls-token');
  await page.locator('#setup-save').click();
  await page.waitForFunction(() => localStorage.getItem('jobtracker.token') === 'account-controls-token');
  await page.waitForFunction(() => document.querySelector('#setup-error').textContent === 'That token was not accepted.');
  assert.equal(await page.evaluate(() => localStorage.getItem('jobtracker.outbox')), queue);
  await otherTab.close();
  assert.deepEqual(errors, []);
  await context.close();
}

async function phoneProfileChecks(browser, url) {
  weights = [
    { uid: 'profile-weight-older', measured_at: '2026-10-02T09:00:00', weight_kg: 80.8, source: 'scale' },
    { uid: 'profile-weight-middle', measured_at: '2026-10-06T09:00:00', weight_kg: 80.4, source: 'scale' },
    { uid: 'profile-weight', measured_at: '2026-10-10T09:00:00', weight_kg: 80.2, source: 'scale' },
  ];
  delays = {}; offline = false;
  active = { uid: 'profile-running', subject_uid: 'subject-a', start_time: '2026-10-10T09:00:00', elapsed_seconds: 1800 };
  const running = JSON.stringify(active), originalOps = ops.length;
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Europe/Amsterdam' });
  const weight = await context.newPage(), tracker = await context.newPage();
  const errors = [];
  for (const page of [weight, tracker]) page.on('pageerror', (error) => errors.push(error.message));
  const rollout = { op_id: 'profile-before-update-op', op: 'add_session', params: { uid: 'profile-before-update-session', subject_uid: 'subject-b', start_time: '2026-10-08T12:00:00', end_time: '2026-10-08T12:10:00' } };
  // Seed the existing installation only. Shared-origin installations must still
  // enroll independently, rather than copying its secret to the second app.
  await context.addInitScript((entry) => {
    const RealDate = Date;
    const now = new RealDate('2026-10-10T09:30:00').getTime();
    window.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
    if (!localStorage.getItem('profile-fixture')) {
      localStorage.setItem('jobtracker.token', 'synthetic-legacy-phone');
      localStorage.setItem('jobtracker.outbox', JSON.stringify([entry]));
      caches.open('jobtracker-legacy-fixture').then((cache) => cache.put('/legacy-marker', new Response('old shell')));
      localStorage.setItem('profile-fixture', 'yes');
    }
  }, rollout);
  const weightHits = hits.length;
  offline = true;
  await weight.goto(`${url}/phone/weight/`);
  await weight.waitForSelector('#weight-whole button');
  await weight.waitForFunction(() => navigator.serviceWorker.controller);
  assert.deepEqual(await weight.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))), [rollout],
    'installing the updated worker preserves an unsent legacy operation');
  assert.equal(await weight.evaluate(async () => (await caches.keys()).includes('jobtracker-legacy-fixture')), false);
  offline = false;
  await weight.locator('#refresh').click();
  await weight.waitForSelector('#weight-history .card');
  await weight.waitForFunction(() => document.querySelector('#sync-status').textContent.startsWith('Synced '));
  assert.equal(await weight.locator('#view-title').textContent(), 'Weight');
  assert.equal(await weight.locator('.tab:visible').count(), 0);
  assert.ok(!hits.slice(weightHits).includes('/api/snapshot'), 'Weight skips the time-history mirror');
  await tracker.goto(`${url}/phone/jobtracker/`);
  await tracker.waitForSelector('#setup:not(.hidden)');
  assert.equal(await tracker.locator('#setup h1').textContent(), 'JobTracker');
  await tracker.locator('#setup-token').fill('synthetic-work-phone');
  await tracker.locator('#setup-save').click();
  await tracker.waitForSelector('#subject-list .card');
  await tracker.waitForFunction(() => document.querySelector('#sync-status').textContent.startsWith('Synced '));
  assert.equal(await tracker.locator('#view-title').textContent(), 'Subjects');
  assert.deepEqual(await tracker.locator('.tab').evaluateAll((tabs) => tabs.map((tab) => tab.dataset.view)), ['goals', 'today', 'sessions', 'graphs']);
  assert.equal(await tracker.locator('#active-card').isVisible(), true);
  assert.equal(ops.length, originalOps + 1, 'launch only flushes the existing operation, without starting a session');
  assert.equal(sessions.filter((row) => row.uid === 'profile-before-update-session').length, 1);
  // Each live app retains its own tab and unfinished form on foreground resume.
  await chooseWeight(weight, 80, 6);
  await tracker.locator('[data-view="sessions"]').click();
  await tracker.locator('#add-session').click();
  const input = tracker.locator('#sheet-body input').first();
  await input.fill('2026-10-09T14:23');
  const draftValue = await input.inputValue();
  for (const page of [weight, tracker]) await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  assert.equal(await weight.locator('#weight-value').textContent(), '80.6 kg');
  assert.equal(await tracker.locator('#view-title').textContent(), 'Sessions');
  assert.equal(await tracker.locator('#sheet').isVisible(), true);
  assert.equal(await input.inputValue(), draftValue);
  await tracker.locator('#sheet-close').click();
  await tracker.locator('[data-view="today"]').click();
  await weight.locator('#weight-clear').click();
  await weight.screenshot({ path: path.join(output, 'phone-weight.png') });
  // Remove the hostile synthetic XSS label from the visual sample only.
  await tracker.locator('#subject-list .card').filter({ hasText: '<img' }).evaluateAll((nodes) => nodes.forEach((node) => node.remove()));
  await tracker.screenshot({ path: path.join(output, 'phone-jobtracker.png') });
  // Screen size has no influence on the product or chosen tab.
  await tracker.setViewportSize({ width: 1280, height: 900 });
  assert.equal(await tracker.locator('.tab').count(), 4);
  await tracker.setViewportSize({ width: 390, height: 844 });
  await weight.locator('#open-settings').click();
  assert.equal(await weight.locator('#settings-day-start').count(), 0);
  assert.match(await weight.locator('#sheet-body').textContent(), /Neither is a full backup/);
  await weight.locator('#sheet-close').click();
  // Wait until the real shared worker has cached every profile and its assets.
  await weight.waitForFunction(() => navigator.serviceWorker.controller);
  await tracker.waitForFunction(() => navigator.serviceWorker.controller);
  await context.setOffline(true);
  // A legacy queued time write remains owned by Weight and must be replayed,
  // even though it no longer has a time-tracking tab.
  const legacy = { op_id: 'profile-legacy-op', op: 'add_session', params: { uid: 'profile-legacy-session', subject_uid: 'subject-b', start_time: '2026-10-09T12:00:00', end_time: '2026-10-09T12:10:00' } };
  await weight.evaluate((entry) => localStorage.setItem('jobtracker.outbox', JSON.stringify([entry])), legacy);
  await weight.goto(`${url}/phone/weight/?offline=1`);
  await weight.waitForSelector('#weight-whole button');
  assert.equal(await weight.locator('.tab:visible').count(), 0);
  await chooseWeight(weight, 80, 7); await weight.locator('#weight-save').click();
  await weight.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 2);
  await tracker.reload(); await tracker.waitForSelector('#subject-list .card');
  assert.equal(await tracker.locator('#view-title').textContent(), 'Subjects');
  assert.equal(await tracker.locator('#active-card').isVisible(), true);
  // Use the real outbox in the second profile, isolated even on one origin.
  await tracker.evaluate(async () => {
    const { send } = await import('/data/outbox.js');
    await send('add_session', { uid: 'profile-work-session', subject_uid: 'subject-b', start_time: '2026-10-09T13:00:00', end_time: '2026-10-09T13:20:00' });
  });
  const beforeReload = await weight.evaluate(() => ({ weight: localStorage.getItem('jobtracker.outbox'), work: localStorage.getItem('jobtracker.phone.jobtracker.outbox') }));
  assert.equal(JSON.parse(beforeReload.weight)[0].op_id, legacy.op_id);
  assert.equal(JSON.parse(beforeReload.work).length, 1);
  await weight.reload(); await tracker.reload();
  await weight.waitForSelector('#weight-history .card'); await tracker.waitForSelector('#subject-list .card');
  assert.equal(await weight.locator('#view-title').textContent(), 'Weight');
  assert.equal(await tracker.locator('#view-title').textContent(), 'Subjects');
  assert.deepEqual(await weight.evaluate(() => ({ weight: localStorage.getItem('jobtracker.outbox'), work: localStorage.getItem('jobtracker.phone.jobtracker.outbox') })), beforeReload);
  // Refresh one app first: it must not drain the other product's local queue.
  rejectProfileAuth = true;
  await context.setOffline(false);
  await weight.locator('#refresh').click();
  await weight.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  await tracker.waitForSelector('#setup:not(.hidden)');
  await tracker.waitForLoadState('networkidle');
  assert.equal(await tracker.evaluate(() => localStorage.getItem('jobtracker.phone.jobtracker.outbox')), beforeReload.work,
    'a rejected token retains the original pending queue');
  assert.equal(await weight.evaluate(() => localStorage.getItem('jobtracker.token')), 'synthetic-legacy-phone');
  rejectProfileAuth = false; dropProfileAck = true;
  await tracker.locator('#setup-token').fill('synthetic-work-phone');
  await tracker.locator('#setup-save').click();
  try { await tracker.waitForFunction(() => document.querySelector('#sync-status').textContent.startsWith('Offline')); }
  catch (error) {
    console.error('phone recovery state', await tracker.evaluate(() => ({ status: document.querySelector('#sync-status').textContent,
      setup: document.querySelector('#setup-error').textContent, pending: localStorage.getItem('jobtracker.phone.jobtracker.outbox') })),
      { dropProfileAck, recentOps: ops.slice(-3) });
    throw error;
  }
  assert.equal(sessions.filter((row) => row.uid === 'profile-work-session').length, 1);
  assert.equal(await tracker.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.phone.jobtracker.outbox')).length), 1,
    'a committed write with a lost acknowledgement remains retryable');
  dropProfileAck = false;
  await tracker.locator('#refresh').click();
  await tracker.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.phone.jobtracker.outbox') || '[]').length === 0);
  await tracker.reload(); await tracker.waitForSelector('#subject-list .card');
  for (const uid of ['profile-legacy-session', 'profile-work-session']) assert.equal(sessions.filter((row) => row.uid === uid).length, 1);
  assert.equal(JSON.stringify(active), running, 'the running timer is never ended or restarted by app launches');
  // Another independent installation receives the same server weight history,
  // with its own storage and one-time credential setup.
  const separate = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const other = await separate.newPage();
  await other.goto(`${url}/phone/weight/`); await other.waitForSelector('#setup:not(.hidden)');
  await other.locator('#setup-token').fill('synthetic-other-phone'); await other.locator('#setup-save').click();
  await other.waitForSelector('#weight-history .card');
  assert.match(await other.locator('#weight-history').textContent(), /80\.7 kg/);
  await separate.close();
  // Cached installations still launch the correct profile with separate storage.
  await tracker.locator('[data-view="graphs"]').click();
  await tracker.waitForSelector('#graph-body .bars');
  await tracker.locator('[data-view="goals"]').click();
  await tracker.waitForSelector('#goal-list .card');
  assert.equal(await tracker.locator('[data-view="weight"]').count(), 0);
  assert.deepEqual(errors, []);
  await context.close();
  // A standalone launch of the root opens Weight, which owns the / identity;
  // Safari/desktop / still provides all five sections.
  const installed = await browser.newContext();
  await installed.addInitScript(() => Object.defineProperty(navigator, 'standalone', { value: true }));
  const legacyPage = await installed.newPage();
  for (const route of ['/', '/index.html']) {
    await legacyPage.goto(url + route);
    await legacyPage.waitForURL(url + '/phone/weight/');
    await legacyPage.waitForSelector('#setup:not(.hidden)');
    assert.equal(await legacyPage.locator('#setup h1').textContent(), 'Weight');
  }
  await installed.close();
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost'); hits.push(url.pathname);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/sync/') || url.pathname === '/ops') {
    if (offline) { req.socket.destroy(); return; }
    if (rejectProfileAuth && req.headers.authorization === 'Bearer synthetic-work-phone') return json(res, { detail: 'Token rejected' }, 401);
    if (delays[url.pathname]) await new Promise((resolve) => setTimeout(resolve, delays[url.pathname]));
    if (url.pathname === '/api/context') {
      if (holdContext) await holdContext;
      return failedContext ? json(res, { detail: 'isolated tracking failure' }, 503) : json(res, { today: currentDay, day_start: '03:00', head, active });
    }
    if (url.pathname === '/api/snapshot') return json(res, { head, subjects, goals, sessions, milestones: [], templates: [], settings: { day_start_time: '03:00' } });
    if (url.pathname === '/sync/pull') return json(res, { changes: changes.filter((r)=>r.seq>Number(url.searchParams.get('since'))), seq: head, head, more: false, settings: { day_start_time: '03:00' } });
    if (url.pathname.startsWith('/api/sessions/day/')) {
      const day = url.pathname.split('/').at(-1);
      return json(res, { sessions: [{ ...sessions[0], subject_name: `Day ${day}`, start_time: `${day}T09:00:00`, end_time: `${day}T10:15:00` }] });
    }
    if (url.pathname === '/api/graphs/breakdown') return json(res, { grouping: 'daily', buckets: [{ date: currentDay, total_seconds: 4500, segments: [{ subject_uid: 'subject-a', subject_name: 'Work', color: '#3B82F6', seconds: 4500 }] }] });
    if (url.pathname === '/api/graphs/agenda') return json(res, { day_start_hour: 3,
      days: Array.from({ length: 30 }, (_, index) => { const day = new Date(`${currentDay}T12:00:00`); day.setDate(day.getDate() - 29 + index); return day.toISOString().slice(0, 10); }),
      sessions: [{ ...sessions[0], day: currentDay, subject_name: 'Work', color: '#3B82F6', start_h: 9, end_h: 10.25 }] });
    if (url.pathname === '/api/weight') {
      getWeightCount++;
      if (failedWeight) { req.socket.destroy(); return; }
      const payload = { today: currentDay, measurements: weights.map((r)=>({...r})) };
      if (holdWeight) await holdWeight;
      return json(res, payload);
    }
    if (url.pathname === '/ops') {
      let body = ''; for await (const chunk of req) body += chunk;
      const submitted = JSON.parse(body).ops;
      if (holdOps) await holdOps;
      const payload = { applied: submitted.map((entry) => ({ op_id: entry.op_id, result: apply(entry) })), seq: head };
      if (dropProfileAck && req.headers.authorization === 'Bearer synthetic-work-phone') {
        req.socket.destroy(); return;
      }
      return json(res, payload);
    }
    return json(res, { detail: 'unknown mock endpoint' }, 404);
  }
  const file = path.join(root, 'web', url.pathname.endsWith('/') ? url.pathname.slice(1) + 'index.html' : url.pathname.slice(1));
  if (!file.startsWith(path.join(root, 'web')) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : file.endsWith('.webmanifest') ? 'application/manifest+json' : file.endsWith('.svg') ? 'image/svg+xml' : 'image/png' });
  fs.createReadStream(file).pipe(res);
});
(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  if (process.env.JOBTRACKER_BROWSER_SCOPE === 'phone-profiles') {
    await phoneProfileChecks(browser, url);
    console.log(JSON.stringify({ browserChecks: 'passed', scope: 'phone-profiles', screenshots: output }));
    await browser.close(); server.close(); return;
  }
  if (process.env.JOBTRACKER_BROWSER_SCOPE === 'icons') {
    await iconChecks(browser, url);
    console.log(JSON.stringify({ browserChecks: 'passed', scope: 'icons', screenshots: output }));
    await browser.close(); server.close(); return;
  }
  if (process.env.JOBTRACKER_BROWSER_SCOPE === 'offline-shell') {
    weights = [{ uid: 'offline-initial', measured_at: '2026-10-10T09:00:00', weight_kg: 85.4, source: 'scale' }];
    await offlineShellCheck(browser, url);
    console.log(JSON.stringify({ browserChecks: 'passed', scope: 'offline-shell' }));
    await browser.close(); server.close(); return;
  }
  if (process.env.JOBTRACKER_BROWSER_SCOPE === 'sessions') {
    await sessionEntryChecks(browser, url);
    console.log(JSON.stringify({ browserChecks: 'passed', scope: 'sessions', screenshots: output }));
    await browser.close(); server.close(); return;
  }
  if (process.env.JOBTRACKER_BROWSER_SCOPE === 'sync-status') {
    await syncStatusChecks(browser, url);
    console.log(JSON.stringify({ browserChecks: 'passed', scope: 'sync-status' }));
    await browser.close(); server.close(); return;
  }
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage(); const errors = [];
  const externalRequests = [];
  await context.route('https://untrusted.test/**', (route) => { externalRequests.push(route.request().url()); return route.abort(); });
  page.on('pageerror', (err) => errors.push(err.message));
  await page.addInitScript(() => {
    localStorage.setItem('jobtracker.token', 'isolated-browser-token');
    const RealDate = Date;
    let now = new RealDate('2026-10-10T09:30:00').getTime();
    window.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
    window.setPickerClock = (value) => { now = new RealDate(value).getTime(); };
  });
  let releaseTracking;
  holdContext = new Promise((resolve) => { releaseTracking = resolve; });
  await page.goto(url); await page.waitForSelector('#weight-entry');
  assert.equal(await page.locator('#view-title').textContent(), 'Weight');
  assert.equal(await page.locator('#weight-entry-controls').evaluate((details) => details.open), false);
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  const entryLayout = await page.evaluate(() => {
    const row = document.querySelector('#weight-whole').getBoundingClientRect();
    return { left: row.left, right: row.right, gapBelowHeader: row.top - document.querySelector('.topbar').getBoundingClientRect().bottom,
      wholeWidth: document.querySelector('#weight-whole button').getBoundingClientRect().width, decimalWidth: document.querySelector('#weight-decimal button').getBoundingClientRect().width };
  });
  assert.ok(entryLayout.left <= 2 && entryLayout.right >= 388 && entryLayout.gapBelowHeader <= 8, JSON.stringify(entryLayout));
  assert.ok(entryLayout.wholeWidth >= 50 && entryLayout.decimalWidth >= 35, JSON.stringify(entryLayout));
  await page.screenshot({ path: path.join(output, 'weight-cold-start-phone.png'), fullPage: true });
  await chooseWeight(page, 80, 7);
  holdContext = null; releaseTracking();
  // If a deliberately held request exceeded the transport deadline on a busy
  // host, resuming it still needs a normal quiet refresh.
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForFunction(() => localStorage.getItem('jobtracker.mirror'));
  assert.equal(await page.locator('#weight-value').textContent(), '80.7 kg', 'weight input is usable before a slow tracking context finishes');
  await clearWeight(page); await entryControls(page, false);
  await page.locator('[data-view="today"]').click(); await page.waitForSelector('#subject-list .card');
  assert.equal(await page.locator('#subject-list img').count(), 0);
  assert.equal(await page.evaluate(() => Boolean(window.injected)), false);
  assert.deepEqual(externalRequests, []);
  assert.equal(await page.locator('#subject-list .card').filter({ hasText: '<img src=x' }).locator('.dot').evaluate((el)=>el.style.backgroundColor), 'rgb(59, 130, 246)');
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  assert.equal(await page.locator('.tab').count(), 5);
  await page.locator('[data-view="goals"]').click();
  assert.deepEqual(await page.locator('#goal-list .name').allTextContents(), ['First goal', 'Focused second goal']);
  await page.locator('#goal-list .name').first().click();
  await page.getByRole('textbox', { name: 'Add milestone to First goal' }).fill('Draft that should survive refresh');
  await page.locator('#refresh').click();
  await page.waitForFunction(() => !document.querySelector('#refresh').hasAttribute('aria-busy'));
  assert.equal(await page.getByRole('textbox', { name: 'Add milestone to First goal' }).inputValue(), 'Draft that should survive refresh');
  await page.evaluate(() => {
    const input = document.querySelector('input[data-milestone-goal]');
    input.focus(); input.setSelectionRange(3, 8);
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForTimeout(50);
  assert.deepEqual(await page.evaluate(() => [document.activeElement.dataset.milestoneGoal, document.activeElement.selectionStart, document.activeElement.selectionEnd]), ['goal-a', 3, 8]);
  // Subject archive/restore is available on the phone and updates via deltas.
  await page.locator('[data-view="today"]').click();
  await page.getByRole('button', { name: 'Edit Reading', exact: true }).click();
  await page.getByRole('button', { name: 'Archive subject', exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('#subject-list .name')].some((el)=>el.textContent==='Reading'));
  await page.locator('#subjects-archived').click();
  await page.getByRole('button', { name: 'Edit Reading', exact: true }).click();
  await page.getByRole('button', { name: 'Restore subject', exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('#subject-list .name')].some((el)=>el.textContent==='Reading'));
  await page.locator('#subjects-active').click();
  assert.equal(await page.getByRole('button', { name: 'Start Reading', exact: true }).count(), 1);
  assert.equal(hits.filter((route)=>route==='/api/snapshot').length, 2, 'only cold load and explicit force refresh fetch snapshots');

  // Day responses arrive in reverse order; the final day stays authoritative.
  await page.locator('[data-view="sessions"]').click(); await page.waitForSelector('#session-list .card');
  delays['/api/sessions/day/2026-10-11'] = 400;
  delays['/api/sessions/day/2026-10-12'] = 20;
  await page.locator('#day-next').click(); await page.locator('#day-next').click();
  await page.waitForFunction(() => document.querySelector('#session-list .name')?.textContent === 'Day 2026-10-12');
  await page.waitForTimeout(450);
  assert.equal(await page.locator('#session-list .name').textContent(), 'Day 2026-10-12');
  await page.locator('#day-date').fill('2026-10-09');
  await page.waitForFunction(() => document.querySelector('#session-list .name')?.textContent === 'Day 2026-10-09');
  await page.locator('#day-today').click();
  await page.waitForFunction(() => document.querySelector('#session-list .name')?.textContent === 'Day 2026-10-10');

  // Agenda blocks open the exact clicked session, including original dates.
  await page.locator('[data-view="graphs"]').click();
  await page.locator('[data-range="all"]').click();
  await page.waitForSelector('.bars');
  await page.locator('[data-mode="agenda"]').click(); await page.waitForSelector('.agenda-block');
  await page.waitForFunction(() => { const scroll = document.querySelector('.agenda-scroll'); return Math.abs(scroll.scrollLeft - (scroll.scrollWidth - scroll.clientWidth)) <= 1; });
  await page.setViewportSize({ width: 844, height: 390 });
  await page.waitForFunction(() => { const scroll = document.querySelector('.agenda-scroll'); return Math.abs(scroll.scrollLeft - (scroll.scrollWidth - scroll.clientWidth)) <= 1; });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => { const scroll = document.querySelector('.agenda-scroll'); return Math.abs(scroll.scrollLeft - (scroll.scrollWidth - scroll.clientWidth)) <= 1; });
  await page.waitForSelector('.agenda-now[data-day="2026-10-10"]');
  assert.equal(await page.locator('.agenda-now').count(), 1);
  assert.equal(await page.locator('.agenda-now-label').textContent(), '09:30');
  const markerTop = await page.locator('.agenda-now').evaluate((marker) => marker.style.top);
  await page.evaluate(() => {
    window.originalAgendaBlock = document.querySelector('.agenda-block');
    const scroll = document.querySelector('.agenda-scroll'); scroll.scrollLeft = 100; scroll.dispatchEvent(new Event('scroll'));
    setPickerClock('2026-10-10T09:31:00'); document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForFunction(() => document.querySelector('.agenda-now-label').textContent === '09:31');
  assert.notEqual(await page.locator('.agenda-now').evaluate((marker) => marker.style.top), markerTop);
  assert.equal(await page.evaluate(() => originalAgendaBlock === document.querySelector('.agenda-block')), true, 'no-head resume moves the marker without replacing session blocks');
  assert.equal(await page.locator('.agenda-scroll').evaluate((scroll) => scroll.scrollLeft), 100);
  await page.locator('#refresh').click(); await page.waitForFunction(() => !document.querySelector('#refresh').hasAttribute('aria-busy'));
  await page.waitForFunction(() => document.querySelector('.agenda-scroll').scrollLeft === 100);
  assert.equal(await page.locator('.agenda-scroll').evaluate((scroll) => scroll.scrollLeft), 100, 'actual data redraw restores horizontal day position');
  await page.evaluate(() => { document.documentElement.requestFullscreen = () => Promise.reject(new Error('native fullscreen unavailable')); });
  await page.locator('#graph-fullscreen').click(); await page.waitForSelector('#view-graphs.plot-fullscreen');
  assert.equal(await page.locator('.agenda-scroll').evaluate((scroll) => scroll.scrollLeft), 100);
  await page.setViewportSize({ width: 844, height: 390 }); await page.waitForTimeout(30);
  assert.equal(await page.locator('.agenda-scroll').evaluate((scroll) => scroll.scrollLeft), 100);
  assert.equal(await page.locator('.agenda-now-label').textContent(), '09:31');
  await page.screenshot({ path: path.join(output, 'agenda-fullscreen-landscape.png'), fullPage: true });
  await page.locator('#graph-fullscreen').click(); await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(30);
  assert.equal(await page.locator('.agenda-scroll').evaluate((scroll) => scroll.scrollLeft), 100);
  await page.evaluate(() => { const scroll = document.querySelector('.agenda-scroll'); scroll.scrollLeft = scroll.scrollWidth; });
  await page.screenshot({ path: path.join(output, 'agenda-now-phone.png'), fullPage: true });
  await page.locator('.agenda-block').click(); await page.waitForSelector('#sheet:not(.hidden)');
  assert.equal(await page.locator('#sheet input[type="datetime-local"]').first().inputValue(), '2026-10-10T09:00');
  await page.locator('#sheet-close').click();

  // Offline timer starts persist through reload, target the same timer when
  // stopped, and retain exact start timestamps for replay after reconnection.
  await page.locator('[data-view="today"]').click();
  offline = true;
  await context.setOffline(true);
  // A double tap starts once; its second tap must not stop and discard the timer.
  await page.getByRole('button', { name: 'Start Work', exact: true }).dblclick();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 1);
  await page.waitForTimeout(150);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox')).length), 1);
  assert.equal(await page.locator('#active-card').isVisible(), true);
  const started = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[0]);
  await page.reload(); await page.waitForSelector('#weight-entry');
  assert.equal(await page.locator('#view-title').textContent(), 'Weight');
  await page.locator('[data-view="today"]').click(); await page.waitForSelector('#active-card:not(.hidden)');
  assert.equal(await page.locator('#active-name').textContent(), 'Work');
  await page.locator('#stop-btn').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 2);
  const stopped = await page.evaluate(() => JSON.parse(localStorage.getItem('jobtracker.outbox'))[1]);
  assert.equal(stopped.params.session_uid, started.params.uid);
  assert.match(started.params.start_time, /^\d{4}-\d{2}-\d{2}T/);
  offline = false; await context.setOffline(false); await page.locator('#refresh').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  assert.equal(ops.find((op)=>op.op==='start_subject').params.start_time, started.params.start_time);

  weights = [{ uid: 'historic', measured_at: '2016-01-01T12:00:00', weight_kg: 77, source: 'historic' },
    { uid: 'scale', measured_at: '2026-10-09T09:00:00', weight_kg: 81.4, source: 'scale' }];
  // Initial history download may complete after a new weighing was saved.
  // Double form submission must persist one operation even while awaiting it.
  let releaseWeight;
  holdWeight = new Promise((resolve) => { releaseWeight = resolve; });
  const firstWeightRequest = getWeightCount;
  await page.evaluate(async () => (await import('/weight.js')).invalidateWeight());
  await page.locator('[data-view="weight"]').click();
  await page.waitForFunction(() => Boolean(document.querySelector('#weight-entry')));
  while (getWeightCount === firstWeightRequest) await page.waitForTimeout(10);
  delays['/ops'] = 150;
  const beforeWeightOps = ops.filter((entry)=>entry.op==='upsert_weight').length;
  assert.equal(await page.locator('#weight-whole button').count(), 7);
  assert.equal(await page.locator('#weight-decimal button').count(), 10);
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  await page.locator('#weight-decimal button[data-value="0"]').click();
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  await page.locator('#weight-whole button[data-value="80"]').click();
  assert.equal(await page.locator('#weight-value').textContent(), '80.0 kg');
  await clearWeight(page);
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  await chooseWeight(page, 80, 7);
  await page.evaluate(() => { const form = document.querySelector('#weight-entry'); form.requestSubmit(); form.requestSubmit(); });
  await savedClear(page);
  assert.equal(ops.filter((entry)=>entry.op==='upsert_weight').length, beforeWeightOps + 1);
  holdWeight = null; releaseWeight();
  await page.waitForFunction(() => document.querySelectorAll('#weight-history .card').length === 3);
  assert.equal(weights.at(-1).weight_kg, 80.7);
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0);
  assert.equal(ops.filter((entry)=>entry.op==='upsert_weight').at(-1).params.measured_at, currentDay);
  assert.equal(weights.at(-1).measured_at, `${currentDay}T00:00:00`);
  assert.match(await page.locator('#weight-history .card').first().textContent(), /80\.7 kg/);
  // Imported scale entries retain their exact measurement time when edited.
  await page.locator('#weight-history .card').filter({ hasText: '81.4 kg' }).click();
  await chooseWeight(page, 81, 6); await page.locator('#weight-save').click();
  await savedClear(page);
  assert.equal(weights.find((row)=>row.uid==='scale').measured_at, '2026-10-09T09:00:00');
  // A late save response cannot clear a newer draft selected while it waited.
  // Hold the response until every draft click completes, regardless of runner speed.
  delays['/ops'] = 0;
  let releaseSave;
  holdOps = new Promise((resolve) => { releaseSave = resolve; });
  await page.locator('#weight-history .card').first().click();
  await chooseWeight(page, 80, 8); await page.locator('#weight-save').click();
  await page.locator('#weight-history .card').filter({ hasText: '81.6 kg' }).click();
  await chooseWeight(page, 82, 1);
  assert.equal(await page.locator('#weight-save').isDisabled(), true, 'the earlier save is still pending while selecting a new draft');
  assert.equal(await page.locator('#weight-value').textContent(), '82.1 kg');
  holdOps = null; releaseSave();
  await page.waitForFunction(() => !document.querySelector('#weight-save').disabled);
  assert.equal(await page.locator('#weight-value').textContent(), '82.1 kg');
  assert.equal(await page.locator('#weight-status').textContent(), 'Previous weighing saved. Current selection is unsaved.');
  assert.equal(await page.locator('#weight-edit-actions').isVisible(), true);
  await page.locator('#weight-cancel').click();
  await page.evaluate(() => { document.querySelector('#views').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, 'weight-entry-phone.png'), fullPage: true });
  await page.locator('#weight-range').selectOption('all'); await page.locator('#weight-options-toggle').click();
  await page.locator('#weight-historic').check();
  assert.match(await page.locator('#weight-canvas').getAttribute('aria-label'), /3 weight measurements/);
  await page.screenshot({ path: path.join(output, 'weight-phone.png'), fullPage: true });
  await page.locator('#weight-range').selectOption('custom');
  await page.locator('#weight-start').fill('2026-10-09'); await page.locator('#weight-end').fill('2026-10-10');
  assert.match(await page.locator('#weight-canvas').getAttribute('aria-label'), /2 weight measurements/);
  await page.locator('#weight-history .card').first().click(); await chooseWeight(page, 80, 9);
  offline = true; await page.locator('#weight-save').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').some((op)=>op.op==='upsert_weight'));
  await page.waitForFunction(() => document.querySelector('#weight-status').textContent.startsWith('Saved on this phone'));
  assert.equal(await page.locator('.weight-tile[aria-pressed="true"]').count(), 0, 'durable offline save clears both parts');
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('#weight-save').isDisabled(), true);
  const queuedNotice = await page.locator('#weight-status').textContent();
  await page.locator('#weight-decimal button[data-value="0"]').click();
  assert.equal(await page.locator('#weight-status').textContent(), queuedNotice, 'editing keeps the offline queue notice');
  await clearWeight(page);
  assert.equal(await page.locator('#weight-status').textContent(), queuedNotice, 'Clear keeps the offline queue notice');
  await page.reload(); await page.locator('[data-view="weight"]').click(); await page.waitForSelector('#weight-history .card');
  assert.match(await page.locator('#weight-history .card').first().textContent(), /80\.9 kg/);
  page.once('dialog', (dialog)=>dialog.accept());
  await page.locator('#weight-history .card').first().click(); await page.locator('#weight-delete').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').some((op)=>op.op==='delete_weight'));
  await page.reload(); await page.locator('[data-view="weight"]').click(); await page.waitForSelector('#weight-history .card');
  assert.equal(await page.locator('#weight-history .card').count(), 2);
  offline = false; await page.locator('#refresh').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 0);
  assert.equal(weights.length, 2);
  // Credential changes clear the weight cache and its form while an earlier
  // save response is still pending. That response must not paint old data.
  await page.locator('#weight-range').selectOption('all');
  await chooseWeight(page, 85, 0);
  delays['/ops'] = 300;
  await page.locator('#weight-save').click();
  await page.evaluate(async () => {
    const api = await import('/api.js'); const weight = await import('/weight.js');
    api.clearToken(); weight.renderWeight(document.querySelector('#weight-body'));
  });
  await page.waitForTimeout(350);
  assert.equal(await page.locator('#weight-value').textContent(), '—.— kg');
  assert.equal(await page.locator('#weight-history .card').count(), 0);
  assert.deepEqual(errors, []);
  await sessionEntryChecks(browser, url);
  const pickerPerformance = await pickerRegressions(browser, url);
  await coldWeightFailure(browser, url);
  await offlineShellCheck(browser, url);
  await accountControlsChecks(browser, url);
  await syncStatusChecks(browser, url);
  await iconChecks(browser, url);
  await phoneProfileChecks(browser, url);
  console.log(JSON.stringify({ browserChecks: 'passed', viewports: ['390x844', '844x390', '1280x900', '320x740'], tested: ['fractional day-start save once, failed disconnect, pending account-change guard', 'session90m preset,170m chunks, offline/ACK-storage durability and duplicate guard', 'shared session ±1day controls, exact UID/duration and fractional timestamps', 'real service-worker offline launch, CSS/modules, queued save and reconnect', 'cold Weight before slow/failed tracking requests', 'viewport-wide whole/decimal tiles', 'durable save resets online/offline', 'storage failure and removed-target retry', 'fullscreen fallback, rotation, draft and account cleanup', 'agenda live clock and day-scroll preservation', 'calendar session navigation and day response race', 'agenda exact session', 'offline timer reload/replay', 'weight load/save race and double submission', 'timestamp and original precision preservation', 'new draft during save and correct receipts', 'weight graphs all/historic/custom', 'offline weight edit/delete/reload', 'account reset', 'latest/backfill anchor', 'manual date collision and same-day coalescing', 'refresh-stable selection and keyboard navigation', 'persistent entry options', 'calendar midnight rollover', '1060-row tap performance'], pickerPerformance, screenshots: output, requests: hits.length }));
  await browser.close(); server.close();
})().catch((err) => { console.error(err); server.close(); process.exit(1); });
