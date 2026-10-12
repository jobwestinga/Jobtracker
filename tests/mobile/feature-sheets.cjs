// Focused real-DOM feature checks. Static local files and injected fake writes;
// this never opens an app database or sends API requests to a real server.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const web = path.resolve(__dirname, '../../web');
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/controls.css">
<link rel="stylesheet" href="/features/goals/styles.css"><link rel="stylesheet" href="/features/settings/styles.css"></head>
<body><button id="add-goal">New goal</button><button id="goal-templates">Recurring goals</button>
<button id="goals-active">Active</button><button id="goals-done">Done</button><div id="goal-list"></div>
<div id="banner"></div><div id="sheet" class="sheet-wrap hidden"><div class="sheet"><h2 id="sheet-title"></h2>
<button id="sheet-close">Close</button><div id="sheet-body"></div></div></div></body></html>`;
const server = http.createServer((req, res) => {
  if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
  const file = path.resolve(web, req.url.split('?')[0].slice(1));
  if (!file.startsWith(web + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css'); fs.createReadStream(file).pipe(res);
});

(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const page = await browser.newPage({ viewport: { width: 320, height: 740 } });
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(async () => {
    const { createGoalsFeature } = await import('/features/goals/index.js');
    const { createTemplatesFeature } = await import('/features/templates.js');
    const { createSettingsFeature } = await import('/features/settings/index.js');
    const { createOrderControls } = await import('/shared/order-controls.js');
    const { closeSheet, bindSheet } = await import('/shared/sheet.js');
    bindSheet();
    window.store = { context: { today: '2026-10-11', day_start: '03:30' }, snapshot: {
      goals: [{ uid: 'g', name: 'Goal', is_completed: 0, is_focused: 0 }, { uid: 'other', name: 'Other', is_completed: 0 }],
      milestones: [{ uid: 'm', goal_uid: 'g', title: 'First milestone', is_done: 0 }], subjects: [], sessions: [], templates: [],
    } };
    window.writes = []; window.generated = 0; window.holdNext = false; window.nextOutcome = { ok: true };
    window.messages = []; window.flushCalls = 0; window.pending = 0; window.failDisconnect = false;
    window.confirm = () => true;
    const banner = (message) => { messages.push(message); document.querySelector('#banner').textContent = message; };
    const act = async (op, params, options = {}) => {
      writes.push({ op, params, uid: options.uid });
      if (options.optimistic) { options.optimistic(); goals.render(); }
      let result = nextOutcome; nextOutcome = { ok: true };
      if (holdNext) { holdNext = false; result = await new Promise((resolve) => { window.finishWrite = resolve; }); }
      if (result.ok && !result.queued && options.afterAcknowledged) await options.afterAcknowledged();
      return result;
    };
    const reorderButtons = createOrderControls({ act, closeSheet });
    window.goals = createGoalsFeature({ store, act, banner, render: () => goals.render(), getView: () => 'goals', reorderButtons });
    window.templates = createTemplatesFeature({ store, act, banner, send: async () => { generated++; return { ok: true }; } });
    window.settings = createSettingsFeature({ store, act, banner, pendingCount: () => pending, refresh: async () => {},
      flush: async () => { flushCalls++; if (window.failFlush) throw Error('acknowledgement storage failed'); return { ok: true, sent: 0 }; },
      clearToken: () => { if (failDisconnect) throw Error('token removal failed'); localStorage.removeItem('jobtracker.token'); },
    });
    goals.mount(); goals.render(); templates.mount();
  });
  const writes = () => page.evaluate(() => window.writes);
  const visible = () => page.locator('#sheet').isVisible();

  // Failure keeps all Goal fields. Durable saves block both repeat Save and
  // competing controls in the same sheet, including ordering.
  await page.locator('#add-goal').click();
  await page.getByLabel('Title', { exact: true }).fill('  ');
  await page.getByRole('button', { name: 'Add goal', exact: true }).click();
  assert.equal((await writes()).length, 0);
  await page.getByLabel('Title', { exact: true }).fill('A draft');
  await page.getByLabel('Description (optional)').fill('Keep these notes');
  await page.evaluate(() => { nextOutcome = { ok: false }; });
  await page.getByRole('button', { name: 'Add goal', exact: true }).click();
  assert.equal(await visible(), true); assert.equal(await page.getByLabel('Title', { exact: true }).inputValue(), 'A draft');
  assert.equal(await page.getByLabel('Description (optional)').inputValue(), 'Keep these notes');
  await page.evaluate(() => { nextOutcome = { ok: false, queued: true }; const save = [...document.querySelectorAll('#sheet button')].find((button) => button.textContent === 'Add goal'); save.click(); save.click(); });
  assert.equal(await visible(), false); assert.equal((await writes()).length, 2);
  await page.getByRole('button', { name: 'Edit Goal', exact: true }).click();
  const beforeOrder = (await writes()).length;
  await page.evaluate(() => {
    [...document.querySelectorAll('#sheet button')].find((button) => button.textContent === 'Move down').click();
    [...document.querySelectorAll('#sheet button')].find((button) => button.textContent === 'Save changes').click();
  });
  assert.equal((await writes()).length, beforeOrder + 1); assert.equal((await writes()).at(-1).op, 'set_goal_order');

  // Focus and milestone toggles cannot invert twice before acknowledgment.
  const beforeFocus = (await writes()).length;
  await page.evaluate(() => { holdNext = true; const star = document.querySelector('.star'); star.click(); star.click(); document.querySelector('.star').click(); });
  assert.equal((await writes()).length, beforeFocus + 1);
  assert.equal(await page.locator('.star').first().isDisabled(), true);
  await page.evaluate(() => finishWrite({ ok: true }));
  await page.waitForFunction(() => !document.querySelector('.star').disabled);
  assert.equal(await page.evaluate(() => store.snapshot.goals[0].is_focused), 1);
  await page.locator('.goal-head > .grow').first().focus(); await page.locator('.goal-head > .grow').first().press('Enter');
  assert.equal(await page.locator('.goal-panel').count(), 1);
  const beforeToggle = (await writes()).length;
  await page.evaluate(() => { holdNext = true; const button = document.querySelector('.ms-row .check'); button.click(); button.click(); document.querySelector('.milestone-title').click(); });
  assert.equal((await writes()).length, beforeToggle + 1);
  await page.evaluate(() => finishWrite({ ok: true }));
  await page.waitForFunction(() => !document.querySelector('.milestone-title').disabled);
  assert.equal(await page.locator('.milestone-title').getAttribute('aria-pressed'), 'true');

  // A new draft is new even if the user types back to the same title. A late
  // request belonging to an earlier account/reset cannot clear the new form.
  const milestone = page.getByRole('textbox', { name: 'Add milestone to Goal', exact: true });
  await milestone.fill('Same title'); await page.evaluate(() => { holdNext = true; });
  await page.getByRole('button', { name: 'Add milestone', exact: true }).click();
  await milestone.fill('Different'); await milestone.fill('Same title');
  await page.evaluate(() => finishWrite({ ok: true }));
  await page.waitForFunction(() => !document.querySelector('input[data-milestone-goal]')?.parentElement.querySelector('button').disabled);
  assert.equal(await milestone.inputValue(), 'Same title');
  await page.evaluate(() => { holdNext = true; });
  await page.getByRole('button', { name: 'Add milestone', exact: true }).click();
  await page.evaluate(() => { goals.reset(); goals.render(); document.querySelector('.goal-head > .grow').click(); });
  await milestone.fill('New account draft');
  await page.evaluate(() => finishWrite({ ok: true }));
  assert.equal(await milestone.inputValue(), 'New account draft');

  // Schedule day is required for weekly/monthly recurrence. Invalid old JSON
  // remains editable, and retained milestone notes survive ordinary edits.
  await page.locator('#goal-templates').click(); await page.getByRole('button', { name: '+ Add recurring goal', exact: true }).click();
  await page.getByLabel('Title ({date} inserts the date)').fill('Weekly');
  await page.getByLabel('Repeats').selectOption('weekly'); await page.getByLabel('Schedule day').fill('');
  const beforeInvalid = (await writes()).length;
  await page.getByRole('button', { name: 'Add recurring goal', exact: true }).click();
  assert.equal((await writes()).length, beforeInvalid); assert.equal(await visible(), true);
  await page.getByLabel('Schedule day').fill('7'); await page.evaluate(() => { nextOutcome = { ok: false }; });
  await page.getByRole('button', { name: 'Add recurring goal', exact: true }).click();
  assert.equal(await visible(), true); assert.equal(await page.getByLabel('Schedule day').inputValue(), '7');
  await page.evaluate(() => {
    const save = [...document.querySelectorAll('#sheet button')].find((button) => button.textContent === 'Add recurring goal'); save.click(); save.click();
  });
  assert.equal((await writes()).length, beforeInvalid + 2); assert.equal(await page.evaluate(() => generated), 1);
  await page.evaluate(() => { store.snapshot.templates = [{ uid: 't', title: 'Imported', recurrence: 'daily', is_active: 1,
    milestones_json: '[null,{"title":"Keep","note":"preserved"},"Plain",{"title":7},{}]' }]; templates.open(); });
  await page.getByRole('button', { name: /Imported daily/ }).click();
  assert.equal(await page.getByLabel('Milestones (one per line)').inputValue(), 'Keep\nPlain');
  await page.getByRole('button', { name: 'Save recurring goal', exact: true }).click();
  assert.deepEqual((await writes()).at(-1).params.milestone_titles, [{ title: 'Keep', note: 'preserved' }, { title: 'Plain', note: '' }]);
  await page.evaluate(() => { store.snapshot.templates[0].milestones_json = '{"bad":"shape"}'; templates.open(); });
  await page.getByRole('button', { name: /Imported daily/ }).click();
  assert.equal(await page.getByLabel('Milestones (one per line)').inputValue(), '');
  await page.locator('#sheet-close').click();

  // Settings retain fractional day boundaries and report flush/credential
  // failures without dropping cached data or allowing a competing save.
  await page.evaluate(() => settings.open());
  assert.equal(await page.getByLabel('Day starts at', { exact: true }).inputValue(), '03:30');
  await page.getByLabel('Day starts at', { exact: true }).fill('');
  const beforeSetting = (await writes()).length;
  await page.getByRole('button', { name: 'Save day start', exact: true }).click();
  assert.equal((await writes()).length, beforeSetting);
  await page.getByLabel('Day starts at', { exact: true }).fill('04:45');
  await page.evaluate(() => { const save = document.querySelector('#settings-day-save'); save.click(); save.click(); });
  assert.equal((await writes()).length, beforeSetting + 1);
  assert.deepEqual((await writes()).at(-1).params, { key: 'day_start_time', value: '04:45' });
  await page.evaluate(() => { settings.open(); window.failFlush = true; });
  await page.getByRole('button', { name: 'Send anything waiting', exact: true }).click();
  assert.equal(await visible(), true); assert.equal(await page.getByRole('button', { name: 'Send anything waiting', exact: true }).isDisabled(), false);
  assert.equal(await page.locator('#banner').textContent(), 'acknowledgement storage failed');
  await page.evaluate(() => { failDisconnect = true; localStorage.setItem('jobtracker.token', 'synthetic-token'); localStorage.setItem('jobtracker.mirror', 'synthetic-cache'); });
  await page.getByRole('button', { name: 'Disconnect this phone', exact: true }).click();
  assert.equal(await page.locator('#banner').textContent(), 'token removal failed');
  assert.deepEqual(await page.evaluate(() => [localStorage.getItem('jobtracker.token'), localStorage.getItem('jobtracker.mirror')]), ['synthetic-token', 'synthetic-cache']);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ featureChecks: 'passed', covered: ['sheet durability and duplicate controls', 'inline toggle locks', 'milestone draft revisions/reset', 'recurrence validation and malformed imports', 'fractional settings and failure handling'], viewport: '320x740' }));
  await browser.close(); server.close();
})().catch((error) => { console.error(error); server.close(); process.exit(1); });
