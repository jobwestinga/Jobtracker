// Real phone DOM + the real action runner/outbox; all writes and storage are
// synthetic and the only server serves local static test assets.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const web = path.resolve(__dirname, '../../web');
const output = process.env.JOBTRACKER_BROWSER_ARTIFACTS || '/tmp/jobtracker-mobile-check';
fs.mkdirSync(output, { recursive: true });
const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/shell.css">
<link rel="stylesheet" href="/styles/controls.css"><link rel="stylesheet" href="/features/subjects/styles.css">
<link rel="stylesheet" href="/features/sessions/styles.css"></head><body><div id="app"><main id="views">
<section class="view"><h1>Subjects</h1><div class="seg"><button id="subjects-active" class="seg-btn">Active</button>
<button id="subjects-archived" class="seg-btn">Archived</button></div><button id="add-subject" class="secondary wide">Add subject</button>
<div id="active-card" class="card active-card"><div id="active-dot" class="active-dot"></div>
<div class="active-body"><div id="active-name"></div><div id="active-elapsed"></div></div><button id="stop-btn" class="danger">Stop</button></div>
<div id="idle-note"></div><div id="subject-list" class="list"></div><div id="banner"></div></section></main></div>
<div id="sheet" class="sheet-wrap hidden"><div class="sheet"><div class="sheet-head"><h2 id="sheet-title"></h2>
<button id="sheet-close" class="icon-btn" aria-label="Close">×</button></div><div id="sheet-body"></div></div></div></body></html>`;
const server = http.createServer((req, res) => {
  if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
  const file = path.resolve(web, req.url.split('?')[0].slice(1));
  if (!file.startsWith(web + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
  fs.createReadStream(file).pipe(res);
});

async function boot(page, mode = 'online') {
  await page.evaluate(async (mode) => {
    const { createSubjectsFeature } = await import('/features/subjects/index.js');
    const { createSessionsFeature } = await import('/features/sessions/index.js');
    const { createOrderControls } = await import('/shared/order-controls.js');
    const { bindSheet, closeSheet } = await import('/shared/sheet.js');
    const { createActionRunner } = await import('/data/actions.js');
    const { send, flush, pendingOperations } = await import('/data/outbox.js');
    const { setToken } = await import('/data/credentials.js');
    const { STORAGE_KEYS } = await import('/shared/storage.js');
    window.now = '2026-10-12T16:40:37';
    const RealDate = window.Date;
    window.Date = class extends RealDate {
      constructor(...args) { super(...(args.length ? args : [window.now])); }
      static now() { return new RealDate(window.now).getTime(); }
    };
    window.store = { context: { today: '2026-10-12', day_start: '03:00' }, snapshot: {
      subjects: [{ uid: 'work', name: 'Work with a longer subject name', color: '#3B82F6', is_archived: 0 },
        { uid: 'reading', name: 'Reading', color: '#10B981', is_archived: 0 },
        { uid: 'archived', name: 'Archived subject', color: '#EF4444', is_archived: 1 }],
      sessions: [{ uid: 'finished', subject_uid: 'archived', start_time: '2026-10-11T09:00:00', end_time: '2026-10-11T10:00:00' }],
    }, active: { uid: 'live', session_uid: 'live', subject_uid: 'reading', start_time: '2026-10-12T15:00:00', timer_origin: Date.now() - 6037e3 } };
    window.originalActive = JSON.stringify(store.active);
    window.writes = []; window.requests = []; window.committed = new Map(); window.networkMode = mode;
    window.outboxWrites = 0; window.failOutboxWrite = 0; window.messages = [];
    window.lastOutcome = null; window.pendingOperations = pendingOperations; window.flush = flush;
    setToken('isolated-quick-add-token');
    const actualSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === STORAGE_KEYS.outbox && ++outboxWrites === failOutboxWrite) throw Error('synthetic storage full');
      return actualSetItem.call(this, key, value);
    };
    window.fetch = async (url, options) => {
      if (url !== '/ops') throw Error(`Unexpected request ${url}`);
      const entries = JSON.parse(options.body).ops;
      requests.push(entries);
      if (networkMode === 'offline') throw Error('isolated offline');
      if (networkMode === 'hold') await new Promise((resolve) => { window.finishNetwork = resolve; });
      const applied = entries.map((entry) => {
        if (!committed.has(entry.op_id)) committed.set(entry.op_id, entry);
        return { op_id: entry.op_id, result: { saved: true } };
      });
      return { ok: true, status: 200, json: async () => ({ applied }) };
    };
    const banner = (message) => { messages.push(message); document.querySelector('#banner').textContent = message; };
    const runAction = createActionRunner({ send, project: () => {}, render: () => subjects.render(),
      refresh: async () => {}, banner, onMutation: () => {} });
    const act = async (op, params, options) => {
      writes.push({ op, params, uid: options?.uid });
      const result = await runAction(op, params, options); lastOutcome = result; return result;
    };
    window.sessions = createSessionsFeature({ store, api: {}, act, banner, saveMirror: () => {}, navigateDay: () => {} });
    sessions.prepare('2026-09-01'); // Direct Subjects entry never inherits a browsed old day.
    window.subjects = createSubjectsFeature({ store, act, banner, editSession: sessions.edit, quickAdd: sessions.quickAdd,
      reorderButtons: createOrderControls({ act, closeSheet }) });
    bindSheet(); subjects.mount(); subjects.render();
  }, mode);
}

async function choose170(page) {
  await page.locator('.session-custom > summary').click();
  for (const minutes of [120, 30, 15, 5]) await page.locator(`.session-duration-chunks [data-minutes="${minutes}"]`).click();
  assert.match(await page.locator('.session-custom-total').textContent(), /170 min/);
}
const minutesOf = (entry) => (Date.parse(`${entry.params.end_time}Z`) - Date.parse(`${entry.params.start_time}Z`)) / 60000;

(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 320, height: 740 }, timezoneId: 'Europe/Amsterdam' });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`); await boot(page);
  const open = () => page.getByRole('button', { name: 'Quick add time to Work with a longer subject name', exact: true }).click();
  const preset = () => page.locator('.session-quick-presets [data-minutes="90"]');
  const waitSaved = () => page.waitForFunction(() => lastOutcome && document.querySelector('#sheet').classList.contains('hidden'));
  const resetOutcome = () => page.evaluate(() => { lastOutcome = null; });
  const activeUnchanged = async () => assert.equal(await page.evaluate(() => JSON.stringify(store.active)), await page.evaluate(() => originalActive));

  // A real keyboard control opens directly into duration selection, with useful
  // width for both the subject name and the >=44px touch targets at 320px.
  const add = page.getByRole('button', { name: 'Quick add time to Work with a longer subject name', exact: true });
  const box = await add.boundingBox(); assert.ok(box.height >= 44 && box.width >= 200);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(output, 'subject-quick-add-320.png') });
  await add.focus(); await add.press('Enter');
  assert.equal(await page.locator('#sheet input[type="datetime-local"]').count(), 0);
  assert.equal(await page.locator('#sheet select').count(), 0);
  const targets = await page.locator('.session-quick-presets button').evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect().height));
  assert.ok(targets.every((height) => height >= 44));
  assert.deepEqual(await page.locator('.session-quick-presets button').evaluateAll((buttons) => buttons.map((button) => Number(button.dataset.minutes))),
    [15, 30, 60, 75, 90, 120]);
  await page.evaluate(() => { const quick = document.querySelector('.session-quick-presets [data-minutes="90"]'); quick.click(); quick.click(); document.querySelector('.session-quick-presets [data-minutes="15"]').click(); });
  await waitSaved();
  let writes = await page.evaluate(() => writes); assert.equal(writes.length, 1); assert.equal(minutesOf(writes[0]), 90);
  assert.equal(writes[0].params.subject_uid, 'work'); assert.equal(writes[0].params.end_time, '2026-10-12T16:40:37');
  assert.match(writes[0].uid, /^[\da-f-]{36}$/); await activeUnchanged();

  // The editor and history reach the same duration owner; chunks stay selected
  // until confirmation and the captured clock cannot drift while saving.
  await page.getByRole('button', { name: 'Edit Work with a longer subject name', exact: true }).click();
  await page.getByRole('button', { name: 'Session history', exact: true }).click();
  await page.getByRole('button', { name: 'Add time, ending now', exact: true }).click();
  await choose170(page);
  const selected = await page.locator('.session-duration-chunks [aria-pressed="true"]').count(); assert.equal(selected, 4);
  const chunks = await page.locator('.session-duration-chunks button').evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect()));
  assert.ok(chunks.every((rect) => rect.height >= 44 && rect.width >= 44 && rect.right <= 320));
  await page.screenshot({ path: path.join(output, 'subject-custom-320.png') });
  await page.evaluate(() => { networkMode = 'hold'; lastOutcome = null; const confirm = document.querySelector('.session-custom-actions .primary'); confirm.click(); confirm.click(); });
  await page.waitForFunction(() => window.finishNetwork);
  await page.evaluate(() => { now = '2026-10-12T16:45:00'; finishNetwork(); }); await waitSaved();
  writes = await page.evaluate(() => writes); assert.equal(writes.length, 2); assert.equal(minutesOf(writes[1]), 170);
  assert.equal(writes[1].params.end_time, '2026-10-12T16:40:37'); await activeUnchanged();

  // Initial persistence failure keeps the selected custom draft for one retry.
  await page.evaluate(() => { networkMode = 'online'; outboxWrites = 0; failOutboxWrite = 1; });
  await open(); await choose170(page); await resetOutcome();
  await page.getByRole('button', { name: 'Add, ending now', exact: true }).click();
  await page.waitForFunction(() => lastOutcome?.ok === false);
  assert.equal(await page.locator('#sheet').isVisible(), true);
  assert.match(await page.locator('.session-custom-total').textContent(), /170 min/);
  assert.equal(await page.evaluate(() => pendingOperations().length), 0);
  assert.equal(await page.evaluate(() => committed.size), 2);
  await page.evaluate(() => { failOutboxWrite = 0; lastOutcome = null; });
  await page.getByRole('button', { name: 'Add, ending now', exact: true }).click(); await waitSaved();
  assert.equal(await page.evaluate(() => committed.size), 3); await activeUnchanged();

  // An acknowledgement-write failure is already durable and cannot invite a
  // new identity; reconnect replays exactly that original operation.
  await page.evaluate(() => { outboxWrites = 0; failOutboxWrite = 2; lastOutcome = null; });
  await open(); await preset().click(); await waitSaved();
  assert.equal(await page.evaluate(() => lastOutcome.queued), true);
  const durable = await page.evaluate(() => pendingOperations()[0]);
  assert.ok(durable.op_id && durable.params.uid); assert.equal(await page.evaluate(() => committed.size), 4);
  const count = await page.evaluate(() => writes.length);
  await page.evaluate(() => document.querySelector('.session-quick-presets [data-minutes="90"]').click());
  assert.equal(await page.evaluate(() => writes.length), count);
  await page.evaluate(async () => { failOutboxWrite = 0; await flush(); });
  assert.equal(await page.evaluate(() => pendingOperations().length), 0);
  assert.equal(await page.evaluate(() => committed.size), 4);
  assert.equal(await page.evaluate(() => requests.at(-1)[0].op_id), durable.op_id);
  await activeUnchanged();

  // Offline entry closes only once, persists through reload, and replays its
  // original exact identity/timestamps without touching the running timer.
  await page.evaluate(() => { networkMode = 'offline'; lastOutcome = null; });
  await open();
  await page.evaluate(() => { const button = document.querySelector('.session-quick-presets [data-minutes="90"]'); button.click(); button.click(); });
  await waitSaved();
  const queued = await page.evaluate(() => pendingOperations()); assert.equal(queued.length, 1); assert.equal(minutesOf(queued[0]), 90);
  await page.reload(); await boot(page, 'offline');
  assert.deepEqual(await page.evaluate(() => pendingOperations()), queued);
  await page.evaluate(async () => { networkMode = 'online'; await flush(); });
  assert.equal(await page.evaluate(() => committed.size), 1); assert.equal(await page.evaluate(() => pendingOperations().length), 0);
  assert.deepEqual(await page.evaluate(() => requests.at(-1)[0]), queued[0]); await activeUnchanged();

  // Archived subjects keep their history and exact session editor, without
  // accidentally exposing a create/start path. Dismissed controls cannot save.
  await page.locator('#subjects-archived').click();
  assert.equal(await page.locator('.subject-quick-add').count(), 0); assert.equal(await page.locator('.subject-start').count(), 0);
  await page.getByRole('button', { name: 'Edit Archived subject', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Add time, ending now', exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Session history', exact: true }).click();
  await page.getByRole('button', { name: /2026-10-11/ }).click();
  assert.equal(await page.locator('#sheet-title').textContent(), 'Edit session');
  assert.equal(await page.getByLabel('Subject', { exact: true }).inputValue(), 'archived');
  await page.locator('#sheet-close').click(); await page.locator('#subjects-active').click();
  await open(); await page.locator('#sheet-close').click();
  const beforeDismissed = await page.evaluate(() => writes.length);
  await page.evaluate(() => document.querySelector('.session-quick-presets [data-minutes="90"]').click());
  assert.equal(await page.evaluate(() => writes.length), beforeDismissed);
  // A saved quick add names its range. One that starts before the day boundary
  // also names the earlier tracking day, so the row is never "missing".
  const receipt = () => page.waitForFunction(() => messages.some((message) => message.startsWith('Added ')))
    .then(() => page.evaluate(() => messages.find((message) => message.startsWith('Added '))));
  await page.evaluate(() => { networkMode = 'online'; lastOutcome = null; messages.length = 0; });
  await open(); await preset().click();
  assert.equal(await receipt(), 'Added 1h 30m to Work with a longer subject name · 15:10–16:40');
  await page.evaluate(() => { now = '2026-10-13T03:20:00'; lastOutcome = null; messages.length = 0; });
  await open(); await page.locator('.session-quick-presets [data-minutes="60"]').click();
  assert.equal(await receipt(), 'Added 1h 00m to Work with a longer subject name · 02:20–03:20 · tracking day: Yesterday');

  // The 75-minute preset follows the same captured, single-write path across
  // midnight as the desktop and preserves the subject's active timer.
  const before75 = await page.evaluate(() => writes.length);
  await page.evaluate(() => { now = '2026-10-13T00:30:37'; lastOutcome = null; });
  await open();
  await page.getByRole('button', { name: 'Add 75 minutes, ending now', exact: true }).click();
  await waitSaved();
  const saved75 = await page.evaluate(() => writes.at(-1));
  assert.equal(await page.evaluate(() => writes.length), before75 + 1);
  assert.equal(minutesOf(saved75), 75);
  assert.equal(saved75.params.start_time, '2026-10-12T23:15:37');
  assert.equal(saved75.params.end_time, '2026-10-13T00:30:37');
  assert.equal(saved75.params.subject_uid, 'work');
  await activeUnchanged();
  await page.evaluate(() => { now = '2026-10-12T16:40:37'; messages.length = 0; });

  // Subject edits keep their draft when saving fails before persistence, and a
  // repeated tap after the retry cannot queue the edit twice.
  await page.evaluate(() => { outboxWrites = 0; failOutboxWrite = 1; lastOutcome = null; });
  await page.getByRole('button', { name: 'Edit Reading', exact: true }).click();
  await page.getByLabel('Name', { exact: true }).fill('Reading notes');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await page.waitForFunction(() => lastOutcome?.ok === false);
  assert.equal(await page.locator('#sheet').isVisible(), true);
  assert.equal(await page.getByLabel('Name', { exact: true }).inputValue(), 'Reading notes');
  const beforeSubjectSave = await page.evaluate(() => writes.length);
  await page.evaluate(() => {
    failOutboxWrite = 0; lastOutcome = null;
    const save = [...document.querySelectorAll('#sheet button')].find((button) => button.textContent === 'Save changes');
    save.click(); save.click();
  });
  await waitSaved();
  assert.equal(await page.evaluate(() => writes.length), beforeSubjectSave + 1);
  assert.deepEqual(await page.evaluate(() => [writes.at(-1).op, writes.at(-1).params.name]), ['update_subject', 'Reading notes']);
  await activeUnchanged();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, 'subject-quick-add-390.png') });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ subjectQuickAdd: 'passed', viewports: ['320x740', '390x844'], covered: [
    'direct keyboard/preset/custom entry', 'stable170m,90m and75m naive timestamps', 'live timer preserved',
    'initial storage failure draft/retry', 'durable ACK failure/replay', 'offline reload stable identity',
    'duplicate and dismissed controls', 'archived history/exact editor', '44px controls and no overflow',
    'saved range and tracking-day receipt', 'subject edit draft retry',
  ], screenshots: output }));
  await browser.close(); server.close();
})().catch((error) => { console.error(error); server.close(); process.exit(1); });
