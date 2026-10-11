import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';


class Events {
  listeners = new Map();
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
  }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  emit(type, event = {}) {
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    for (const callback of [...(this.listeners.get(type) || [])]) callback(event);
    return event;
  }
}

class Element {
  constructor(document, tag = 'div') {
    this.document = document; this.tagName = tag; this.children = [];
    this.attributes = new Map(); this.classes = new Set(); this.hidden = false;
    this.disabled = false; this.isConnected = true; this.scrollTop = 0;
    this.classList = {
      add: (...names) => names.forEach((name) => this.classes.add(name)),
      remove: (...names) => names.forEach((name) => this.classes.delete(name)),
      contains: (name) => this.classes.has(name),
    };
  }
  append(...children) {
    for (const child of children) { child.parentNode = this; this.children.push(child); }
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  focus() { this.document.activeElement = this; }
  contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
  getClientRects() { return this.hidden ? [] : [{}]; }
  querySelectorAll() {
    const result = [];
    const visit = (node) => {
      for (const child of node.children) {
        if ((!child.disabled && ['button', 'select', 'input', 'summary'].includes(child.tagName)) ||
            child.getAttribute('tabindex') === '0' || (child.tagName === 'a' && child.getAttribute('href'))) result.push(child);
        visit(child);
      }
    };
    visit(this); return result;
  }
}

async function setup() {
  const document = new Events();
  document.documentElement = new Element(document, 'html');
  document.body = new Element(document, 'body');
  document.documentElement.append(document.body);
  const views = new Element(document), sheet = new Element(document);
  sheet.classList.add('hidden'); document.body.append(views, sheet);
  document.querySelector = (selector) => selector === '#views' ? views : selector === '#sheet' ? sheet : null;
  document.fullscreenElement = null;
  let exits = 0;
  document.exitFullscreen = () => {
    exits++; document.fullscreenElement = null; document.emit('fullscreenchange');
    return Promise.resolve();
  };
  const target = new Element(document);
  const before = new Element(document), after = new Element(document);
  views.append(before, target, after);
  const input = new Element(document, 'input'), closeButton = new Element(document, 'button');
  input.value = '86.4'; target.append(input, closeButton);
  const trigger = new Element(document, 'button'); document.body.append(trigger); trigger.focus();
  globalThis.document = document;
  const module = await importWebModule('shared/fullscreen.js');
  return { document, views, sheet, target, before, after, input, closeButton, trigger, module, exits: () => exits };
}

async function settled() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
function nativeRequest(world) {
  world.document.documentElement.requestFullscreen = () => {
    world.document.fullscreenElement = world.document.documentElement;
    world.document.emit('fullscreenchange');
    return Promise.resolve();
  };
}

test('unsupported native fullscreen opens viewport fallback and preserves DOM, draft, focus and scroll', async () => {
  const w = await setup(); const changes = [];
  w.target.setAttribute('tabindex', '3'); w.target.setAttribute('role', 'figure'); w.target.setAttribute('aria-modal', 'false');
  w.views.scrollTop = 127;
  const originalChildren = [...w.views.children];
  const controller = w.module.createFullscreenController(w.target, { onChange: (open) => changes.push(open) });
  controller.toggle();
  assert.equal(controller.isOpen, true);
  assert.ok(w.target.classList.contains('plot-fullscreen'));
  assert.ok(w.document.body.classList.contains('has-plot-fullscreen'));
  assert.deepEqual(w.views.children, originalChildren);
  assert.equal(w.target.parentNode, w.views);
  assert.equal(w.input.value, '86.4');
  assert.equal(w.document.activeElement, w.target);
  assert.equal(w.target.getAttribute('role'), 'dialog');
  w.views.scrollTop = 456;
  controller.close();
  assert.equal(controller.isOpen, false);
  assert.equal(w.views.scrollTop, 127);
  assert.equal(w.document.activeElement, w.trigger);
  assert.equal(w.target.getAttribute('tabindex'), '3');
  assert.equal(w.target.getAttribute('role'), 'figure');
  assert.equal(w.target.getAttribute('aria-modal'), 'false');
  assert.equal(w.input.value, '86.4');
  assert.deepEqual(changes, [true, false]);
  controller.destroy();
});

test('rejected or synchronously throwing native request leaves a usable fallback', async () => {
  for (const failure of ['reject', 'throw']) {
    const w = await setup();
    w.document.documentElement.requestFullscreen = () => {
      if (failure === 'throw') throw Error('not permitted');
      return Promise.reject(Error('not supported'));
    };
    const controller = w.module.createFullscreenController(w.target);
    controller.toggle(); await settled();
    assert.equal(controller.isOpen, true);
    assert.ok(w.target.classList.contains('plot-fullscreen'));
    controller.close(); assert.equal(controller.isOpen, false);
    assert.equal(w.exits(), 0); controller.destroy();
  }
});

test('Escape closes active plot, while a visible editing sheet retains keyboard ownership', async () => {
  const w = await setup(); const controller = w.module.createFullscreenController(w.target);
  controller.toggle(); w.sheet.classList.remove('hidden');
  w.document.emit('keydown', { key: 'Escape' });
  assert.equal(controller.isOpen, true);
  w.sheet.classList.add('hidden');
  const event = w.document.emit('keydown', { key: 'Escape' });
  assert.equal(event.defaultPrevented, true); assert.equal(controller.isOpen, false);
  assert.equal(w.document.activeElement, w.trigger); controller.destroy();
});

test('Tab wraps only visible enabled plot controls and restores focus on exit', async () => {
  const w = await setup();
  const disabled = new Element(w.document, 'button'); disabled.disabled = true;
  const hidden = new Element(w.document, 'button'); hidden.hidden = true;
  w.target.append(disabled, hidden);
  const controller = w.module.createFullscreenController(w.target); controller.toggle();
  w.document.emit('keydown', { key: 'Tab' });
  assert.equal(w.document.activeElement, w.input);
  w.document.emit('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(w.document.activeElement, w.closeButton);
  w.document.emit('keydown', { key: 'Tab' });
  assert.equal(w.document.activeElement, w.input);
  controller.close(); assert.equal(w.document.activeElement, w.trigger); controller.destroy();
});

test('an empty plot traps Tab on its root without throwing', async () => {
  const w = await setup(); w.target.children = [];
  const controller = w.module.createFullscreenController(w.target); controller.toggle();
  const event = w.document.emit('keydown', { key: 'Tab' });
  assert.equal(event.defaultPrevented, true); assert.equal(w.document.activeElement, w.target);
  controller.destroy();
});

test('destroy closes the plot, removes listeners, restores absent attributes and cannot reopen', async () => {
  const w = await setup(); const controller = w.module.createFullscreenController(w.target);
  controller.toggle(); controller.destroy(); controller.destroy(); controller.toggle();
  assert.equal(controller.isOpen, false); assert.equal(w.target.getAttribute('tabindex'), null);
  assert.equal(w.target.getAttribute('role'), null); assert.equal(w.target.getAttribute('aria-modal'), null);
  assert.equal(w.document.listeners.get('keydown').size, 0);
  assert.equal(w.document.listeners.get('fullscreenchange').size, 0);
  assert.equal(w.document.body.classList.contains('has-plot-fullscreen'), false);
});

test('only one controller opens and handoff restores the new trigger on close', async () => {
  const w = await setup();
  const secondTarget = new Element(w.document), secondTrigger = new Element(w.document, 'button');
  w.views.append(secondTarget); w.document.body.append(secondTrigger);
  const first = w.module.createFullscreenController(w.target), second = w.module.createFullscreenController(secondTarget);
  first.toggle(); secondTrigger.focus(); second.toggle();
  assert.equal(first.isOpen, false); assert.equal(second.isOpen, true);
  assert.equal(w.target.classList.contains('plot-fullscreen'), false);
  assert.equal(w.document.body.classList.contains('has-plot-fullscreen'), true);
  w.module.closeFullscreen();
  assert.equal(second.isOpen, false); assert.equal(w.document.activeElement, secondTrigger);
  first.destroy(); second.destroy();
});

test('native exit closes the active controller even when an earlier inactive controller owns the first listener', async () => {
  const w = await setup(); nativeRequest(w);
  const secondTarget = new Element(w.document); w.views.append(secondTarget);
  const first = w.module.createFullscreenController(w.target), second = w.module.createFullscreenController(secondTarget);
  second.toggle(); await settled();
  assert.equal(second.isOpen, true);
  w.document.fullscreenElement = null; w.document.emit('fullscreenchange');
  assert.equal(second.isOpen, false);
  assert.equal(w.document.body.classList.contains('has-plot-fullscreen'), false);
  first.destroy(); second.destroy();
});

test('a native request resolving after Close is released without reopening the plot', async () => {
  const w = await setup(); let release;
  w.document.documentElement.requestFullscreen = () => new Promise((resolve) => {
    release = () => { w.document.fullscreenElement = w.document.documentElement; w.document.emit('fullscreenchange'); resolve(); };
  });
  const controller = w.module.createFullscreenController(w.target);
  controller.toggle(); controller.close(); release(); await settled();
  assert.equal(controller.isOpen, false); assert.equal(w.exits(), 1);
  assert.equal(w.document.fullscreenElement, null); assert.equal(w.input.value, '86.4'); controller.destroy();
});

test('a delayed native request belongs to the currently open fallback and closing it releases native mode', async () => {
  const w = await setup(); let release;
  w.document.documentElement.requestFullscreen = () => new Promise((resolve) => {
    release = () => { w.document.fullscreenElement = w.document.documentElement; w.document.emit('fullscreenchange'); resolve(); };
  });
  const secondTarget = new Element(w.document); w.views.append(secondTarget);
  const first = w.module.createFullscreenController(w.target), second = w.module.createFullscreenController(secondTarget);
  first.toggle(); first.close(); second.toggle(); release(); await settled();
  assert.equal(second.isOpen, true); assert.equal(w.exits(), 0);
  second.close(); await settled(); assert.equal(w.exits(), 1);
  first.destroy(); second.destroy();
});

test('finishing an earlier native exit cannot close a newly opened fallback', async () => {
  const w = await setup(); nativeRequest(w); let finishExit;
  w.document.exitFullscreen = () => new Promise((resolve) => {
    finishExit = () => { w.document.fullscreenElement = null; w.document.emit('fullscreenchange'); resolve(); };
  });
  const secondTarget = new Element(w.document); w.views.append(secondTarget);
  const first = w.module.createFullscreenController(w.target), second = w.module.createFullscreenController(secondTarget);
  first.toggle(); await settled(); first.close(); second.toggle(); finishExit(); await settled();
  assert.equal(second.isOpen, true);
  assert.ok(w.document.body.classList.contains('has-plot-fullscreen'));
  second.destroy(); first.destroy();
});
