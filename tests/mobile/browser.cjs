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
let head = 1, offline = false, active = null, ops = [], weights = [], delays = {}, holdWeight = null,
  holdContext = null, failedContext = false, failedWeight = false;
let getWeightCount = 0;
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
  await entryControls(page, true); await page.locator('#weight-clear').click();
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
  assert.match(await page.locator('#weight-entry-day').textContent(), /7.*Oct.*2026/);
  assert.match(await page.locator('#weight-entry-controls summary').getAttribute('aria-label'), /7.*Oct.*2026/);
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
  await page.locator('#weight-fullscreen').click(); await page.waitForSelector('#weight-plot.plot-fullscreen');
  assert.equal(await page.locator('#weight-fullscreen').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.evaluate(() => nativeFullscreenAttempts), 1);
  assert.equal(await page.evaluate(() => originalWeightPlot === document.querySelector('#weight-body > #weight-plot')), true);
  await page.waitForFunction(() => document.querySelector('#weight-canvas').clientHeight > 400);
  const portraitHeight = await page.locator('#weight-canvas').evaluate((canvas) => canvas.clientHeight);
  await page.screenshot({ path: path.join(output, 'weight-fullscreen-portrait.png'), fullPage: true });
  await page.setViewportSize({ width: 844, height: 390 });
  await page.waitForFunction(() => { const canvas = document.querySelector('#weight-canvas'); return canvas.width === Math.round(canvas.clientWidth * Math.min(2, devicePixelRatio)) && canvas.height === Math.round(canvas.clientHeight * Math.min(2, devicePixelRatio)); });
  const landscape = await page.locator('#weight-canvas').evaluate((canvas) => ({ width: canvas.clientWidth, height: canvas.clientHeight }));
  assert.ok(landscape.width > 780 && landscape.height < portraitHeight && landscape.height >= 120, JSON.stringify(landscape));
  await page.screenshot({ path: path.join(output, 'weight-fullscreen-landscape.png'), fullPage: true });
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#weight-plot').evaluate((plot) => plot.classList.contains('plot-fullscreen')), false);
  assert.equal(await page.locator('#weight-fullscreen').getAttribute('aria-pressed'), 'false');
  assert.equal(await page.locator('#weight-value').textContent(), '89.9 kg', 'fullscreen leaves the entry draft untouched');
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
  await page.waitForTimeout(40);
  const performance = await page.evaluate(async () => {
    const canvas = document.querySelector('#weight-canvas'), ctx = canvas.getContext('2d');
    let fills = 0, historyChanges = 0;
    const originalFill = ctx.fillRect; ctx.fillRect = function(...args) { fills++; return originalFill.apply(this, args); };
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
  assert.deepEqual(errors, []);
  await context.close();
  return performance;
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
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost'); hits.push(url.pathname);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/sync/') || url.pathname === '/ops') {
    if (offline) { req.socket.destroy(); return; }
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
      return json(res, { applied: submitted.map((entry) => ({ op_id: entry.op_id, result: apply(entry) })), seq: head });
    }
    return json(res, { detail: 'unknown mock endpoint' }, 404);
  }
  const file = path.join(root, 'web', url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
  if (!file.startsWith(path.join(root, 'web')) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : file.endsWith('.webmanifest') ? 'application/manifest+json' : 'image/png' });
  fs.createReadStream(file).pipe(res);
});
(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
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
  await page.waitForFunction(() => document.querySelector('#refresh').textContent === '↻');
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
  await page.locator('#refresh').click(); await page.waitForFunction(() => document.querySelector('#refresh').textContent === '↻');
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
  await page.getByRole('button', { name: 'Start Work', exact: true }).click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('jobtracker.outbox') || '[]').length === 1);
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
  await page.locator('#weight-history .card').first().click();
  await chooseWeight(page, 80, 8); await page.locator('#weight-save').click();
  await page.locator('#weight-history .card').filter({ hasText: '81.6 kg' }).click();
  await chooseWeight(page, 82, 1);
  await page.waitForFunction(() => !document.querySelector('#weight-save').disabled);
  assert.equal(await page.locator('#weight-value').textContent(), '82.1 kg');
  assert.equal(await page.locator('#weight-status').textContent(), 'Previous weighing saved. Current selection is unsaved.');
  assert.equal(await page.locator('#weight-edit-actions').isVisible(), true);
  delays['/ops'] = 0;
  await page.locator('#weight-cancel').click();
  await page.evaluate(() => { document.querySelector('#views').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, 'weight-entry-phone.png'), fullPage: true });
  await page.locator('#weight-range').selectOption('all'); await page.locator('.weight-options summary').click();
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
  const pickerPerformance = await pickerRegressions(browser, url);
  await coldWeightFailure(browser, url);
  console.log(JSON.stringify({ browserChecks: 'passed', viewports: ['390x844', '844x390', '1280x900', '320x740'], tested: ['cold Weight before slow/failed tracking requests', 'viewport-wide whole/decimal tiles', 'durable save resets online/offline', 'storage failure and removed-target retry', 'fullscreen fallback, rotation, draft and account cleanup', 'agenda live clock and day-scroll preservation', 'calendar session navigation and day response race', 'agenda exact session', 'offline timer reload/replay', 'weight load/save race and double submission', 'timestamp and original precision preservation', 'new draft during save and correct receipts', 'weight graphs all/historic/custom', 'offline weight edit/delete/reload', 'account reset', 'latest/backfill anchor', 'manual date collision and same-day coalescing', 'refresh-stable selection and keyboard navigation', 'persistent entry options', 'calendar midnight rollover', '1060-row tap performance'], pickerPerformance, screenshots: output, requests: hits.length }));
  await browser.close(); server.close();
})().catch((err) => { console.error(err); server.close(); process.exit(1); });
