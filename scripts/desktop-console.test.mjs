import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../desktop/ui/console.js', import.meta.url), 'utf8');
function page(fetch) {
  const nodes = new Map();
  const listeners = new Map();
  const timers = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, { value: '', checked: false, hidden: false, disabled: false, textContent: '', addEventListener() {} });
    return nodes.get(id);
  };
  const snapshot = {
    mode: 'wsl', preferences: {}, runtime: { ready: true, services: [], selection: {}, checked_at: 1 },
    preview: { confirmation: 'fixture', database_exists: true, selection: {}, preparation: { ready: true }, ports: [8000, 3100] },
  };
  const context = vm.createContext({
    document: { getElementById: node, querySelectorAll: () => [], documentElement: { dataset: {} }, visibilityState: 'visible', addEventListener: (name, callback) => listeners.set(name, callback) },
    window: { confirm: () => true, addEventListener: (name, callback) => listeners.set(name, callback) },
    location: { hash: '#fixture' }, history: { replaceState() {} }, sessionStorage: { setItem() {} },
    fetch: (...args) => Promise.resolve().then(() => fetch(...args)), AbortController,
    setInterval() {}, setTimeout: (callback) => { const id = Symbol(); timers.set(id, callback); return id; }, clearTimeout: (id) => timers.delete(id),
  });
  vm.runInContext(source, context);
  return { node, listeners, timers, run: (code) => vm.runInContext(code, context), snapshot };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const response = (state) => ({ ok: true, json: async () => state });

test('a hung status request times out and a later refresh recovers without changing the draft', async () => {
  let calls = 0;
  let fixture;
  const ui = page((_path, options) => {
    calls += 1;
    if (calls === 1) return new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(Object.assign(new Error(), { name: 'AbortError' }))));
    return Promise.resolve(response(fixture));
  });
  fixture = ui.snapshot;
  ui.node('env-file').value = '/draft/private.env';
  ui.run('dirty = true; loaded = true');
  await tick();
  assert.equal(ui.timers.size, 1, 'requests must have a deadline');
  [...ui.timers.values()][0]();
  await tick();
  assert.match(ui.node('connection').textContent, /无法连接/);
  await ui.run('refresh()');
  assert.match(ui.node('connection').textContent, /已连接/);
  assert.equal(ui.node('env-file').value, '/draft/private.env');
  assert.equal(ui.timers.size, 0);
});

test('lost connection hides stale health and disables actions even when an input renders again', async () => {
  let failing = false;
  let fixture;
  const ui = page(() => failing ? Promise.reject(new Error('offline')) : Promise.resolve(response(fixture)));
  fixture = ui.snapshot;
  await tick();
  await ui.run('refresh()');
  assert.equal(ui.node('open-app').hidden, false);
  failing = true;
  ui.node('confirm').checked = true;
  await ui.run('refresh()');
  ui.run('render()');
  assert.equal(ui.node('open-app').hidden, true);
  assert.equal(ui.node('start').disabled, true);
  assert.equal(ui.node('inspect').disabled, true);
  assert.equal(ui.node('confirm').checked, false);
  assert.match(ui.node('runtime-state').textContent, /未确认/);
});

test('returning to a visible page rechecks status but never repeats a control operation', async () => {
  const paths = [];
  let fixture;
  const ui = page((path) => { paths.push(path); return Promise.resolve(response(fixture)); });
  fixture = ui.snapshot;
  await tick();
  ui.node('database').value = '/draft/keep.sqlite';
  ui.run('loaded = true; dirty = true');
  assert.equal(typeof ui.listeners.get('visibilitychange'), 'function');
  ui.listeners.get('visibilitychange')();
  await tick();
  assert.ok(paths.length >= 2);
  assert.ok(paths.every((path) => path === '/api/state'));
  assert.equal(ui.node('database').value, '/draft/keep.sqlite');
});

test('a control request timeout does not replay the operation and the deadline covers its body', async () => {
  let fixture;
  let posts = 0;
  let signal;
  const ui = page((path, options) => {
    if (options.method !== 'POST') return Promise.resolve(response(fixture));
    posts += 1;
    signal = options.signal;
    return Promise.resolve({ ok: true, json: () => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(Object.assign(new Error(), { name: 'AbortError' })))) });
  });
  fixture = ui.snapshot;
  await tick();
  const request = ui.run('api("/api/stop", {})');
  await tick();
  assert.equal(ui.timers.size, 1);
  [...ui.timers.values()][0]();
  await assert.rejects(request, /操作可能已被接收/);
  await ui.run('refresh()');
  assert.equal(posts, 1);
  assert.equal(signal.aborted, true);
  assert.equal(ui.timers.size, 0);
});
