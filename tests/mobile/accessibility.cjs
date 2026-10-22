// Real-DOM keyboard/reflow checks using static local assets and synthetic data.
// This does not run a real server, access an app database or test VoiceOver.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const web = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8')
  .replace('<script type="module" src="/app.js"></script>', '');
const server = http.createServer((req, res) => {
  if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
  const file = path.resolve(web, req.url.split('?')[0].slice(1));
  if (!file.startsWith(web + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});

(async () => {
  let browser;
  try {
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
    const page = await browser.newPage({ viewport: { width: 320, height: 740 }, hasTouch: true });
    const errors = []; page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(async () => {
      const { createGoalsFeature } = await import('/features/goals/index.js');
      const { createGraphsFeature } = await import('/features/graphs/index.js');
      const { createWeightFeature } = await import('/features/weight/index.js');
      const { createSettingsFeature } = await import('/features/settings/index.js');
      const { createTemplatesFeature } = await import('/features/templates.js');
      const { createSubjectsFeature } = await import('/features/subjects/index.js');
      const { createSessionsFeature } = await import('/features/sessions/index.js');
      const { createOrderControls } = await import('/shared/order-controls.js');
      const { bindSheet, closeSheet } = await import('/shared/sheet.js');
      const { localDate, addDays } = await import('/shared/time.js');
      const today = localDate(), yesterday = addDays(today, -1);
      document.querySelector('#setup').classList.add('hidden');
      document.querySelector('#app').classList.remove('hidden');
      window.currentView = 'weight'; window.writes = []; window.notices = []; window.holdNext = false;
      window.nextOutcome = { ok: true }; window.daysOpened = []; window.edited = [];
      window.store = { context: { today, day_start: '03:00' }, active: null, snapshot: {
        goals: [{ uid: 'goal', name: 'An accessible goal', is_completed: 0, is_focused: 0 }],
        milestones: [{ uid: 'milestone', goal_uid: 'goal', title: 'A milestone', is_done: 0 }],
        subjects: [{ uid: 'subject', name: 'Physics', color: '#3B82F6' }],
        sessions: [{ uid: 'session', subject_uid: 'subject', start_time: `${today}T09:00:00`, end_time: `${today}T10:30:00`, duration_seconds: 5400 }], templates: [],
      } };
      const banner = (message) => notices.push(message);
      const act = async (op, params, options = {}) => {
        writes.push({ op, params });
        if (options.optimistic) { options.optimistic(); goals.render(); }
        let outcome = nextOutcome; nextOutcome = { ok: true };
        if (holdNext) { holdNext = false; outcome = await new Promise((resolve) => { window.finishWrite = resolve; }); }
        return outcome;
      };
      const api = {
        breakdown: async () => ({ grouping: 'daily', buckets: [
          { date: addDays(today, -2), total_seconds: 3600, segments: [{ subject_uid: 'subject', subject_name: 'Physics', seconds: 3600, color: '#3B82F6' }] },
          { date: yesterday, total_seconds: 5400, segments: [
            { subject_uid: 'subject', subject_name: 'Physics', seconds: 1800, color: '#3B82F6' },
            { subject_uid: 'writing', subject_name: 'Writing', seconds: 1800, color: '#14B8A6' },
            { subject_uid: 'subject', subject_name: 'Physics', seconds: 1800, color: '#3B82F6' },
          ] },
          { date: today, total_seconds: 41040, segments: [{ subject_uid: 'subject', subject_name: 'Physics', seconds: 41040, color: '#3B82F6' }] },
        ] }),
        agenda: async () => ({ days: [yesterday, today], day_start: '03:00', sessions: [
          { ...store.snapshot.sessions[0], day: today, start_h: 9, end_h: 10.5, subject_name: 'Physics', color: '#3B82F6' },
        ] }),
      };
      window.goals = createGoalsFeature({ store, act, banner, render: () => goals.render(), getView: () => currentView,
        reorderButtons: createOrderControls({ act, closeSheet }) });
      window.graphs = createGraphsFeature({ store, api, banner, getView: () => currentView,
        navigateDay: (day) => daysOpened.push(day), editSession: (session) => edited.push(session.uid) });
      window.settings = createSettingsFeature({ store, act, banner, flush: async () => ({ ok: true }), pendingCount: () => 2,
        refresh: async () => {}, clearToken: () => {}, getSyncStatus: () => ({ label: 'Offline · 2 waiting', lastSuccessAt: 1234567890000, error: 'Synthetic network timeout' }) });
      window.templates = createTemplatesFeature({ store, act, send: async () => ({ ok: true }), banner });
      window.sessions = createSessionsFeature({ store, api: { day: async () => ({ sessions: store.snapshot.sessions.map((row) => ({ ...row, subject_name: 'Physics', color: '#3B82F6' })) }) },
        act, banner, saveMirror: () => {}, navigateDay: () => {} });
      window.subjects = createSubjectsFeature({ store, act, banner, reorderButtons: createOrderControls({ act, closeSheet }),
        editSession: sessions.edit, quickAdd: sessions.quickAdd });
      goals.mount(); graphs.mount(); templates.mount(); subjects.mount(); sessions.mount(); bindSheet();
      const measurements = [
        { uid: 'weight-older', measured_at: `${yesterday}T09:00:00`, weight_kg: 82.5, source: 'scale' },
        { uid: 'weight-latest', measured_at: `${today}T09:00:00.123456`, weight_kg: 83.960123, source: 'scale' },
      ];
      window.weight = createWeightFeature({ api: { weights: async () => ({ today, measurements, aliases: [] }) },
        uuid: () => 'new-weight', getToken: () => 'synthetic-account', pendingOperations: () => [],
        send: async (op, params) => { writes.push({ op, params }); return { ok: true, queued: true }; },
      });
      window.showView = async (view) => {
        currentView = view;
        for (const section of document.querySelectorAll('#views > .view')) section.classList.toggle('hidden', section.id !== `view-${view}`);
        for (const tab of document.querySelectorAll('.tab')) {
          tab.classList.toggle('on', tab.dataset.view === view); tab.setAttribute('aria-current', tab.dataset.view === view ? 'page' : 'false');
        }
        document.querySelector('#view-title').textContent = view[0].toUpperCase() + view.slice(1);
        if (view === 'weight') weight.render(document.querySelector('#weight-body'));
        if (view === 'goals') goals.render();
        if (view === 'graphs') { await graphs.load(); graphs.render(); }
        if (view === 'today') subjects.render();
        if (view === 'sessions') { sessions.prepare(today); await sessions.load(); sessions.render(); }
      };
      document.querySelector('#open-settings').onclick = settings.open;
      for (const tab of document.querySelectorAll('.tab')) tab.onclick = () => showView(tab.dataset.view);
      await showView('weight');
    });
    await page.waitForFunction(() => document.querySelectorAll('.weight-history-row').length === 2);

    // Picker remains two full-width rows; keyboard selects both explicitly.
    const tileRows = await page.evaluate(() => ['weight-whole', 'weight-decimal'].map((id) => {
      const row = document.getElementById(id), boxes = [...row.children].map((button) => button.getBoundingClientRect());
      return { count: boxes.length, tops: [...new Set(boxes.map((box) => box.top))], minHeight: Math.min(...boxes.map((box) => box.height)),
        width: row.getBoundingClientRect().width, right: Math.max(...boxes.map((box) => box.right)) };
    }));
    assert.deepEqual(tileRows.map((row) => row.count), [7, 10]);
    assert.ok(tileRows.every((row) => row.tops.length === 1 && row.minHeight >= 44 && row.width >= 310 && row.right <= 320));
    assert.equal(await page.locator('#weight-save').isDisabled(), true);
    await page.locator('#weight-whole button[tabindex="0"]').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#weight-whole button[aria-pressed="true"]').count(), 1);
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#weight-decimal button[aria-pressed="true"]').count(), 1);
    assert.equal(await page.locator('#weight-save').isDisabled(), false);
    const focusBefore = await page.evaluate(() => document.activeElement.outerHTML);
    await page.evaluate(() => { weight.invalidate(); weight.render(document.querySelector('#weight-body')); });
    assert.equal(await page.evaluate(() => document.activeElement.outerHTML), focusBefore, 'refresh does not steal picker focus');
    await page.locator('#weight-canvas').focus(); await page.keyboard.press('Home');
    assert.match(await page.locator('#weight-chart-detail').textContent(), /82\.5 kg/);
    await page.keyboard.press('End'); assert.match(await page.locator('#weight-chart-detail').textContent(), /83\.960123 kg/);
    // Plot options are one disclosure button; the history list carries the readings.
    const options = page.getByRole('button', { name: 'Plot options' });
    assert.equal(await options.getAttribute('aria-expanded'), 'false');
    await options.press('Enter');
    assert.equal(await options.getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('#weight-plot-options').isVisible(), true);
    await options.press('Enter');

    // A reusable sheet traps focus, keeps inert background announcements available,
    // preserves a rejected draft, and restores the invoker on Escape.
    await page.locator('#open-settings').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('.sheet').getAttribute('role'), 'dialog');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'sheet-title');
    assert.equal(await page.evaluate(() => document.querySelector('#app').inert), true);
    assert.equal(await page.evaluate(() => document.querySelector('#announcements').inert), false);
    assert.match(await page.locator('#sheet-body').textContent(), /Offline · 2 waiting/);
    assert.match(await page.locator('#sheet-body').textContent(), /Synthetic network timeout/);
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement.id), 'sheet-close');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Disconnect this phone');
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement.id), 'sheet-close');
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'open-settings');
    assert.equal(await page.evaluate(() => document.querySelector('#app').inert), false);

    await page.evaluate(() => showView('goals'));
    const primaryContrast = await page.locator('#add-goal').evaluate((node) => {
      const luminance = (color) => {
        const channels = color.match(/[\d.]+/g).slice(0, 3).map((number) => {
          const value = Number(number) / 255;
          return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
        });
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
      };
      const style = getComputedStyle(node), foreground = luminance(style.color), background = luminance(style.backgroundColor);
      return (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05);
    });
    assert.ok(primaryContrast >= 4.5, `primary button contrast ${primaryContrast}`);
    await page.locator('#add-goal').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'sheet-title');
    await page.getByLabel('Title', { exact: true }).fill('Keep keyboard draft');
    await page.evaluate(() => { nextOutcome = { ok: false }; });
    await page.getByRole('button', { name: 'Add goal', exact: true }).click();
    assert.equal(await page.getByLabel('Title', { exact: true }).inputValue(), 'Keep keyboard draft');
    assert.equal(await page.locator('#sheet').isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Add goal');
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'add-goal');
    await page.locator('.goal-head > [role="button"]').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('.goal-head > [role="button"]').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.goalFocus), 'expand:goal');
    assert.ok(await page.evaluate(() => parseFloat(getComputedStyle(document.activeElement).outlineWidth) >= 3));
    await page.evaluate(() => goals.render());
    assert.equal(await page.evaluate(() => document.activeElement.dataset.goalFocus), 'expand:goal');
    await page.locator('.star').focus(); await page.keyboard.press('Space');
    await page.waitForFunction(() => !document.querySelector('.star').disabled);
    assert.equal(await page.locator('.star').getAttribute('aria-pressed'), 'true');

    // Readable recurrence descriptions are linked to controls rather than a tooltip.
    await page.locator('#goal-templates').click();
    await page.getByRole('button', { name: '+ Add recurring goal', exact: true }).click();
    await page.getByLabel('Repeats', { exact: true }).selectOption('weekly');
    const describedBy = await page.getByLabel('Schedule day', { exact: true }).getAttribute('aria-describedby');
    assert.match(await page.locator(`#${describedBy}`).textContent(), /Monday/);
    await page.keyboard.press('Escape');

    // Graph data and edit/day actions are reachable with a keyboard.
    await page.evaluate(() => showView('graphs'));
    const bars = await page.locator('.bar-col').evaluateAll((columns) => columns.map((column) => {
      const fill = column.querySelector('.bar-fill');
      const pieces = [...fill.querySelectorAll('.bar-seg')];
      return { fill: fill.getBoundingClientRect().height, stack: column.querySelector('.bar-stack').getBoundingClientRect().height,
        portions: pieces.map((piece) => Number.parseFloat(piece.style.height)),
        paintedPortions: pieces.map((piece) => piece.getBoundingClientRect().height / fill.getBoundingClientRect().height * 100),
        painted: pieces.reduce((sum, piece) => sum + piece.getBoundingClientRect().height, 0),
        value: column.querySelector('.bar-val').textContent };
    }));
    assert.deepEqual(bars.map((bar) => bar.value), ['1.0h', '1.5h', '11.4h']);
    assert.deepEqual(bars.map((bar) => bar.portions.length), [1, 2, 1], 'sessions are summed by subject before drawing');
    assert.ok(Math.abs(bars[0].fill / bars[1].fill - 2 / 3) < .005, `bar-height ratio ${JSON.stringify(bars)}`);
    for (const bar of bars) {
      assert.ok(Math.abs(bar.portions.reduce((sum, portion) => sum + portion, 0) - 100) < .001, 'subject proportions fill exactly 100%');
      assert.ok(Math.abs(bar.painted - bar.fill) < 1, 'painted segments preserve the represented duration');
      assert.ok(bar.portions.every((portion, index) => Math.abs(portion - bar.paintedPortions[index]) < .1), 'each painted subject share matches its duration');
    }
    assert.ok(Math.abs(bars[0].stack - bars[1].stack) < 1, 'hour labels share one baseline');
    async function checkHourLabels() {
      const labels = await page.locator('.bar-val span').evaluateAll((nodes) => nodes.map((node) => {
        const style = getComputedStyle(node), rect = node.getBoundingClientRect(), column = node.closest('.bar-col').getBoundingClientRect();
        return { size: Number.parseFloat(style.fontSize), shadow: style.textShadow,
          border: style.borderTopWidth, background: style.backgroundColor,
          fits: rect.left >= column.left - 1 && rect.right <= column.right + 1 };
      }));
      assert.ok(labels[0].size < labels[1].size && labels[1].size * 1.25 < labels[2].size, JSON.stringify(labels));
      assert.equal(labels[0].shadow, 'none');
      assert.match(labels[2].shadow, /239, 68, 68/, 'above-10h totals have a red glow');
      assert.ok(labels.every((label) => label.border === '0px' && label.background === 'rgba(0, 0, 0, 0)' && label.fits), JSON.stringify(labels));
    }
    await checkHourLabels();
    await page.locator('[data-mode="bars"]').focus();
    await page.keyboard.press('ArrowUp');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.mode), 'bars');
    await page.keyboard.press('ArrowDown');
    await page.waitForSelector('.agenda-block');
    assert.equal(await page.locator('[data-mode="agenda"]').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.mode), 'agenda');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.mode), 'agenda');
    await page.keyboard.press('ArrowUp');
    await page.waitForSelector('.bar-col');
    assert.equal(await page.locator('[data-mode="bars"]').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.mode), 'bars');
    await page.screenshot({ path: '/tmp/jobtracker-mobile-graph-review.png' });
    await page.locator('.bar-col').last().focus(); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => daysOpened.length), 1);
    assert.match(await page.locator('.bar-col').last().getAttribute('aria-label'), /Physics/);
    await page.locator('.bar-col').last().tap();
    assert.equal(await page.evaluate(() => daysOpened.length), 2);
    await page.locator('[data-mode="agenda"]').tap();
    await page.waitForSelector('.agenda-block');
    assert.equal(await page.locator('[data-mode="agenda"]').getAttribute('aria-pressed'), 'true');
    await page.locator('.agenda-block').focus(); await page.keyboard.press('Space');
    assert.deepEqual(await page.evaluate(() => edited), ['session']);
    await page.locator('.agenda-daylbl').last().focus(); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => daysOpened.length), 3);

    // Subject quick add is a labelled dialog: keyboard entry, named preset
    // group, exposed chunk toggle state, and focus returns to its invoker.
    await page.evaluate(() => showView('today'));
    await page.getByRole('button', { name: 'Quick add time to Physics', exact: true }).focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'sheet-title');
    assert.equal(await page.getByRole('group', { name: 'Quick add, ending now' }).getByRole('button').count(), 6);
    await page.locator('.session-custom > summary').focus(); await page.keyboard.press('Enter');
    const chunk = page.locator('.session-duration-chunks [data-minutes="30"]');
    await chunk.focus(); await page.keyboard.press('Space');
    assert.equal(await chunk.getAttribute('aria-pressed'), 'true');
    assert.match(await page.locator('#session-custom-total').textContent(), /30 min/);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Quick add time to Physics');
    // Colour swatches are named toggles rather than unlabeled coloured buttons.
    await page.locator('#add-subject').click();
    assert.equal(await page.getByRole('group', { name: 'Colour' }).getByRole('button').count(), 10);
    await page.getByRole('button', { name: 'Teal', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Teal', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByRole('button', { name: 'Blue, already used', exact: true }).count(), 1);
    await page.keyboard.press('Escape');

    // Sheet Escape must close only the upper dialog when plot listener was added later.
    await page.evaluate(() => showView('weight'));
    await page.locator('#weight-fullscreen').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('#weight-plot').getAttribute('aria-label'), 'Weight over time');
    assert.equal(await page.evaluate(() => document.querySelector('#announcements').inert), false);
    await page.evaluate(() => settings.open()); await page.keyboard.press('Escape');
    assert.equal(await page.locator('#sheet').isVisible(), false);
    assert.equal(await page.locator('#weight-plot').evaluate((node) => node.classList.contains('plot-fullscreen')), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'weight-fullscreen');
    assert.equal(await page.locator('#weight-plot').evaluate((node) => node.classList.contains('plot-fullscreen')), false);

    // Background changes repaint the same graph controls without dropping a
    // keyboard reader's focused day or session onto the page body.
    await page.evaluate(async () => {
      await showView('graphs');
      document.querySelector('[data-mode="bars"]').click();
    });
    await page.waitForSelector('.bar-col[role="button"]');
    const bar = page.locator('.bar-col[role="button"]').first();
    await bar.focus();
    const key = await bar.getAttribute('data-focus-key');
    await page.evaluate(() => {
      graphs.data.data.buckets[0].total_seconds += 60;
      graphs.data.data.buckets[0].segments[0].seconds += 60;
      graphs.render();
    });
    assert.equal(await page.evaluate(() => document.activeElement.dataset.focusKey), key);

    // Text reflow at 320 CSS pixels, without document-level horizontal overflow.
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    for (const view of ['weight', 'goals', 'graphs', 'today', 'sessions']) {
      await page.evaluate((view) => showView(view), view);
      const metrics = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth,
        view: document.querySelector(`#view-${currentView}`).scrollWidth,
        viewClient: document.querySelector(`#view-${currentView}`).clientWidth,
        tabRight: Math.max(...[...document.querySelectorAll('.tab')].map((tab) => tab.getBoundingClientRect().right)) }));
      assert.ok(metrics.document <= metrics.width + 1 && metrics.view <= metrics.viewClient + 1 && metrics.tabRight <= metrics.width + 1,
        `${view} text reflow: ${JSON.stringify(metrics)}`);
    }
    await page.evaluate(() => showView('graphs'));
    await checkHourLabels();
    await page.locator('#open-settings').click();
    assert.ok(await page.locator('.sheet').evaluate((node) => node.scrollWidth <= node.clientWidth + 1));
    await page.keyboard.press('Escape');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.evaluate(() => {
      const node = document.createElement('div'); node.style.animation = 'pulse 1s infinite'; document.body.append(node);
      const animation = getComputedStyle(node).animationName; node.remove(); return animation;
    }), 'none');
    // Zooming in stays available; pinching below 1x (which shrank the whole app) does not.
    const viewport = await page.locator('meta[name="viewport"]').getAttribute('content');
    assert.ok(!/user-scalable\s*=\s*no/i.test(viewport) && !/maximum-scale/i.test(viewport) && /minimum-scale=1/.test(viewport), viewport);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ accessibilityChecks: 'passed', viewport: '320x740', textSize: '200%',
      covered: ['sheet focus/Escape/draft recovery', 'fullscreen nested dialog and focus restoration', 'keyboard goals/graphs/weight', 'graph arrow focus, touch and proportional stacked bars', 'subject quick add and colour names', 'lazy chart text data', 'contrast/reflow/reduced motion'], voiceOverTested: false }));
  } finally { await browser?.close(); server.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
