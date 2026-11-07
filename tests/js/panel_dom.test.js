// Интеграционная проверка веб-панели в jsdom (нужен пакет jsdom; без него тест пропускается).
// Проверяет поведение, а не вёрстку: layout в jsdom не считается.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { JSDOM = null; }
const STATIC = path.join(__dirname, '../../picarpro/web/static');

function boot(opts) {
  opts = opts || {};
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8')
    .replace(/<script src="[^"]+"><\/script>/g, '');
  const dom = new JSDOM(html, { url: 'http://pi.local/', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  const sockets = [];
  class FakeWS {
    constructor(url) { this.url = url; this.sent = []; this.readyState = 0; sockets.push(this); }
    send(m) { this.sent.push(JSON.parse(m)); }
    close() { this.readyState = 3; if (this.onclose) this.onclose(); }
    open() { this.readyState = 1; if (this.onopen) this.onopen(); }
    msg(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
  }
  FakeWS.OPEN = 1; FakeWS.CONNECTING = 0;
  w.WebSocket = FakeWS;
  const fetchLog = [];
  w.fetch = opts.fetch ? (u, o) => { fetchLog.push([u, o]); return opts.fetch(u, o); }
    : () => Promise.resolve({ status: 200, json: () => Promise.resolve({ lines: [] }) });
  w.prompt = () => opts.promptValue || '';
  w.confirm = () => true;
  w.Element.prototype.setPointerCapture = function () {};
  w.Element.prototype.releasePointerCapture = function () {};
  const rect = { left: 0, top: 0, width: 140, height: 140, right: 140, bottom: 140 };
  w.Element.prototype.getBoundingClientRect = function () { return rect; };
  w.eval(fs.readFileSync(path.join(STATIC, 'panel-core.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
  const ws = sockets[0];
  ws.open();
  return { w, ws, sockets, doc: w.document, fetchLog };
}

function pointer(w, el, type, id, x, y) {
  const e = new w.Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(e, {
    pointerId: { value: id }, clientX: { value: x }, clientY: { value: y },
  });
  el.dispatchEvent(e);
}
function key(w, type, k) {
  const e = new w.KeyboardEvent(type, { key: k, bubbles: true, cancelable: true });
  w.document.dispatchEvent(e);
}
const moves = (ws) => ws.sent.filter((m) => m.cmd === 'move').map((m) => m.params.dir);

test('hello отправляется при открытии с id устройства', { skip: !JSDOM }, () => {
  const { ws } = boot();
  const hello = ws.sent.find((m) => m.cmd === 'hello');
  assert.ok(hello, 'hello не отправлен');
  assert.match(hello.params.id, /^c[a-z0-9]+$/);
  assert.strictEqual(hello.params.visible, true);
});

test('два пальца: отпускание камерного джойстика не останавливает колёса', { skip: !JSDOM }, () => {
  const { w, ws, doc } = boot();
  const wheels = doc.getElementById('joystick-container');
  const cam = doc.getElementById('cam-joystick-container');
  pointer(w, wheels, 'pointerdown', 1, 70, 10);      // вперёд
  pointer(w, cam, 'pointerdown', 2, 100, 70);        // камера вправо
  pointer(w, cam, 'pointerup', 2, 100, 70);          // камеру отпустили
  assert.ok(!moves(ws).includes('stop'), 'колёса остановились при отпускании камеры');
  assert.deepStrictEqual(moves(ws), ['forward']);
  pointer(w, wheels, 'pointerup', 1, 70, 10);
  assert.strictEqual(moves(ws).pop(), 'stop');
});

test('чужой pointerId игнорируется (камера не читает палец колёс)', { skip: !JSDOM }, () => {
  const { w, ws, doc } = boot();
  const wheels = doc.getElementById('joystick-container');
  pointer(w, wheels, 'pointerdown', 1, 70, 10);
  const before = ws.sent.length;
  pointer(w, wheels, 'pointermove', 99, 140, 140);
  assert.strictEqual(ws.sent.length, before);
});

test('клавиши: W+D = диагональ, отпустили D = вперёд, отпустили W = стоп', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  key(w, 'keydown', 'w');
  key(w, 'keydown', 'd');
  key(w, 'keyup', 'd');
  key(w, 'keyup', 'w');
  assert.deepStrictEqual(moves(ws), ['forward', 'forward_right', 'forward', 'stop']);
});

test('Q/E — разворот на месте, пробел — стоп, Esc — аварийный стоп', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  key(w, 'keydown', 'q');
  assert.strictEqual(moves(ws).pop(), 'spin_left');
  key(w, 'keydown', ' ');
  assert.strictEqual(moves(ws).pop(), 'stop');
  key(w, 'keydown', 'Escape');
  assert.ok(ws.sent.some((m) => m.cmd === 'estop'));
});

test('потеря фокуса окна отправляет стоп', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  key(w, 'keydown', 'w');
  w.dispatchEvent(new w.Event('blur'));
  assert.strictEqual(moves(ws).pop(), 'stop');
});

test('дельта статуса не затирает показания отсутствующих полей', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  ws.msg({ type: 'status', delta: false, data: {
    cpu_temp: 50, mpu6050: { roll: 1, pitch: 2, accel: { x: 0, y: 0, z: 1 }, gyro: { x: 0, y: 0, z: 0 } },
    ir_left: 1, low_voltage: true, clients: 2, driver: null } });
  assert.match(doc.getElementById('sb-imu').textContent, /R:1/);
  assert.strictEqual(doc.getElementById('ir-left-val').textContent, 'LINE');
  assert.strictEqual(doc.getElementById('low-voltage-banner').style.display, 'block');
  ws.msg({ type: 'status', delta: true, data: { cpu_temp: 55 } });
  assert.strictEqual(doc.getElementById('sb-cpu-temp').textContent, '55\u00B0C');
  assert.match(doc.getElementById('sb-imu').textContent, /R:1/, 'IMU затёрт дельтой');
  assert.strictEqual(doc.getElementById('ir-left-val').textContent, 'LINE');
  assert.strictEqual(doc.getElementById('low-voltage-banner').style.display, 'block');
  assert.strictEqual(doc.getElementById('sb-clients').textContent, '2');
});

test('водитель — другое устройство: плашка и класс; ответ not_driver не спамит', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  ws.msg({ type: 'status', delta: true, data: { driver: 'someone-else' } });
  assert.ok(doc.body.classList.contains('not-driver'));
  assert.strictEqual(doc.getElementById('driver-chip').dataset.state, 'other');
  ws.msg({ type: 'response', data: { ok: false, code: 'not_driver', driver: 'someone-else' } });
  ws.msg({ type: 'response', data: { ok: false, code: 'not_driver', driver: 'someone-else' } });
  assert.strictEqual(doc.querySelectorAll('.toast').length, 1);
  doc.getElementById('take-control-btn').click();
  assert.ok(ws.sent.some((m) => m.cmd === 'take_control'));
  assert.ok(!doc.body.classList.contains('not-driver'));
});

test('пока вкладка скрыта, статус копится и применяется одним разом', { skip: !JSDOM }, () => {
  const { w, ws, doc } = boot();
  Object.defineProperty(doc, 'hidden', { value: true, configurable: true });
  ws.msg({ type: 'status', delta: false, data: { cpu_temp: 60 } });
  ws.msg({ type: 'status', delta: true, data: { cpu_usage: 7 } });
  assert.notStrictEqual(doc.getElementById('sb-cpu-temp').textContent, '60\u00B0C');
  Object.defineProperty(doc, 'hidden', { value: false, configurable: true });
  doc.dispatchEvent(new w.Event('visibilitychange'));
  assert.strictEqual(doc.getElementById('sb-cpu-temp').textContent, '60\u00B0C');
  assert.strictEqual(doc.getElementById('sb-cpu-usage').textContent, '7%');
});

test('переподключение: растущая пауза, а не фиксированные 3 с', { skip: !JSDOM }, () => {
  const { w, ws, sockets } = boot();
  const delays = [];
  const orig = w.setTimeout;
  w.setTimeout = (fn, ms) => { delays.push(ms); return orig(fn, 1e9); };
  ws.close();
  assert.strictEqual(sockets.length, 1);
  assert.ok(delays.some((d) => d >= 300 && d <= 1000), 'первая пауза должна быть < 1 с: ' + delays);
});

const lastCmd = (ws, cmd) => ws.sent.filter((m) => m.cmd === cmd).pop();

test('миссии: при подключении запрашивается список, он рисуется в select', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  assert.deepStrictEqual(lastCmd(ws, 'mission').params, { action: 'list' });
  ws.msg({ type: 'response', data: { ok: true, cmd: 'mission', missions: [
    { name: 'square', builtin: true, steps: 8, sec: 12 }, { name: 'trip', builtin: false, steps: 3, sec: 4 }],
    active: false, recording: false, has_path: false, state: 'idle' } });
  const sel = doc.getElementById('mission-select');
  assert.strictEqual(sel.options.length, 2);
  assert.strictEqual(doc.getElementById('mission-del').style.display, 'none');   // встроенную не удалить
  sel.value = 'trip';
  sel.dispatchEvent(new doc.defaultView.Event('change'));
  assert.strictEqual(doc.getElementById('mission-del').style.display, '');
});

test('миссии: старт отправляет выбранное имя; во время работы Start заблокирован', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  ws.msg({ type: 'response', data: { ok: true, cmd: 'mission', missions: [
    { name: 'square', builtin: true, steps: 8, sec: 12 }], active: false, state: 'idle' } });
  doc.getElementById('mission-start').click();
  assert.deepStrictEqual(lastCmd(ws, 'mission').params, { action: 'start', name: 'square' });
  ws.msg({ type: 'status', delta: true, data: { mission: { active: true, name: 'square', done: 2, total: 8, state: 'running' } } });
  assert.strictEqual(doc.getElementById('mission-status').textContent, 'Running square: 2/8');
  assert.ok(doc.getElementById('mission-start').disabled);
  assert.ok(!doc.getElementById('mission-stop').disabled);
  doc.getElementById('mission-stop').click();
  assert.deepStrictEqual(lastCmd(ws, 'mission').params, { action: 'stop' });
  ws.msg({ type: 'status', delta: true, data: { mission: { active: false, name: 'square', state: 'obstacle', done: 3, total: 8 } } });
  assert.match(doc.getElementById('mission-status').textContent, /obstacle/);
  assert.ok(!doc.getElementById('mission-start').disabled);
});

test('запись пути: старт, стоп с именем, «домой» доступна после записи', { skip: !JSDOM }, () => {
  const { ws, doc } = boot({ promptValue: 'trip' });
  assert.ok(doc.getElementById('mission-home').disabled);
  doc.getElementById('mission-rec').click();
  assert.deepStrictEqual(lastCmd(ws, 'mission').params, { action: 'record_start' });
  ws.msg({ type: 'response', data: { ok: true, cmd: 'mission', action: 'record_start', recording: true, rec_events: 0, active: false, has_path: false, state: 'idle' } });
  assert.strictEqual(doc.getElementById('mission-rec').textContent, 'Stop & save');
  doc.getElementById('mission-rec').click();
  assert.deepStrictEqual(lastCmd(ws, 'mission').params, { action: 'record_stop', save_as: 'trip' });
  ws.msg({ type: 'response', data: { ok: true, cmd: 'mission', recording: false, has_path: true, active: false, saved: true, state: 'idle' } });
  assert.ok(!doc.getElementById('mission-home').disabled);
  doc.getElementById('mission-home').click();
  assert.deepStrictEqual(lastCmd(ws, 'mission').params, { action: 'return_home' });
});

test('обновление: версия и резервные копии из /api/update/info; откат отправляет имя', { skip: !JSDOM }, async () => {
  const info = { ok: true, version: '1.5.3', pending: null,
    backups: [{ name: 'backup_1.5.2_20261105-101010.tar.gz', size: 2048000, ts: 1 }] };
  const { doc, fetchLog } = boot({ fetch: (u) => Promise.resolve({ status: 200,
    json: () => Promise.resolve(String(u).includes('/api/update') ? (String(u).includes('rollback') ? { ok: true, from: '1.5.3', to: '1.5.2', restarting: false } : info) : { lines: [] }) }) });
  await new Promise((r) => setTimeout(r, 30));
  assert.strictEqual(doc.getElementById('upd-version').textContent, 'v1.5.3');
  assert.strictEqual(doc.getElementById('upd-backups').options.length, 1);
  assert.ok(!doc.getElementById('upd-rollback').disabled);
  doc.getElementById('upd-rollback').click();
  await new Promise((r) => setTimeout(r, 30));
  const call = fetchLog.find(([u, o]) => String(u).includes('/api/update/rollback'));
  assert.ok(call, 'запрос отката не отправлен');
  assert.strictEqual(JSON.parse(call[1].body).name, 'backup_1.5.2_20261105-101010.tar.gz');
  assert.match(doc.getElementById('upd-status').textContent, /1\.5\.3 \u2192 1\.5\.2/);
});

test('обновление: без выбранного файла — подсказка, запрос не уходит', { skip: !JSDOM }, () => {
  const { doc, fetchLog } = boot();
  const before = fetchLog.length;
  doc.getElementById('upd-go').click();
  assert.match(doc.getElementById('upd-status').textContent, /Choose an archive/);
  assert.strictEqual(fetchLog.length, before);
});
