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
  w.eval(fs.readFileSync(path.join(STATIC, 'translate/ru.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(STATIC, 'translate/en.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(STATIC, 'panel-core.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
  // Язык по умолчанию — русский; исторические проверки ниже написаны под
  // английские строки — включаем EN явно (RU проверяется отдельным тестом).
  w.eval('setLang("en")');
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
  assert.strictEqual(doc.getElementById('mission-status').textContent, 'Running Square: 2/8');  // встроенная миссия показывается локализованным именем
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

// ===================== v1.6.8: новые проверки =====================

test('i18n: русский — язык по умолчанию, EN переключается', { skip: !JSDOM }, () => {
  const { w, ws, doc } = boot();
  // boot() включил EN; проверяем переключение обратно на RU (по умолчанию)
  w.eval('setLang("ru")');
  assert.strictEqual(doc.documentElement.lang, 'ru');
  assert.strictEqual(doc.getElementById('estop-btn').textContent, 'СТОП');
  assert.strictEqual(doc.querySelector('[data-i18n="card_cv"]').textContent, 'Компьютерное зрение');
  ws.msg({ type: 'status', delta: false, data: { ir_left: 1 } });
  assert.strictEqual(doc.getElementById('ir-left-val').textContent, 'ЛИНИЯ');
  // и обратно на EN
  w.eval('setLang("en")');
  assert.strictEqual(doc.documentElement.lang, 'en');
  assert.strictEqual(doc.getElementById('estop-btn').textContent, 'STOP');
});

test('i18n: словари RU/EN покрывают все data-i18n ключи и совпадают между собой', { skip: !JSDOM }, () => {
  const { doc, w } = boot();
  const htmlKeys = [...doc.querySelectorAll('[data-i18n]')].map((el) => el.dataset.i18n);
  const titleKeys = [...doc.querySelectorAll('[data-i18n-title]')].map((el) => el.dataset.i18nTitle);
  const ru = w.eval('Object.keys(I18N.ru)');
  const en = w.eval('Object.keys(I18N.en)');
  for (const k of [...htmlKeys, ...titleKeys]) {
    assert.ok(ru.includes(k), 'нет RU-ключа: ' + k);
    assert.ok(en.includes(k), 'нет EN-ключа: ' + k);
  }
  // у русского есть лишняя форма склонения «_few» (2-4 строки/шага) — в английском её нет
  const ruCore = ru.filter((k) => !/_few$/.test(k));
  assert.deepStrictEqual([...ruCore].sort(), [...en].sort(), 'наборы ключей RU и EN различаются');
  for (const k of ru) {
    assert.notStrictEqual(w.eval(`I18N.ru[${JSON.stringify(k)}]`), '', 'пустой RU-ключ: ' + k);
  }
});

test('servo-control: каналы 3 и 4 убраны, клешня (5,6) на месте', { skip: !JSDOM }, () => {
  const { doc } = boot();
  const inputs = [...doc.querySelectorAll('input.servo-angle-input')].map((i) => i.dataset.servo);
  assert.ok(!inputs.includes('3'), 'канал 3 должен отсутствовать');
  assert.ok(!inputs.includes('4'), 'канал 4 должен отсутствовать');
  assert.ok(inputs.includes('0'), 'нет рулевого');
  assert.ok(inputs.includes('5'), 'нет наклона клешни');
  assert.ok(inputs.includes('6'), 'нет захвата клешни');
});

test('режимы: в Autonomous — Radar/Maze Exit/IR Line, в CV — Mask без YOLO/YOLO Sem', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  const autoBtns = [...doc.querySelectorAll('#auto-group .gbtn')].map((b) => b.dataset.auto);
  assert.deepStrictEqual([...autoBtns].sort(), ['mazeExit', 'radarScan', 'trackLine']);
  for (const removed of ['automatic', 'keepDistance', 'obstacleGrab', 'mazeU']) {
    assert.ok(!autoBtns.includes(removed), 'режим удалён: ' + removed);
  }
  assert.ok(!autoBtns.includes('stop'), 'Stop заменён на отдельную аварийную кнопку');
  // CV-режимы: YOLO (рамки) и YOLO Sem удалены, «YOLO Mask» (yoloSeg) остался
  const cvBtns = [...doc.querySelectorAll('.cv-btn[data-cv]')].map((b) => b.dataset.cv);
  for (const m of ['none', 'findColor', 'findlineCV', 'trackHand', 'watchDog', 'yoloSeg']) {
    assert.ok(cvBtns.includes(m), 'нет CV-режима ' + m);
  }
  assert.ok(!cvBtns.includes('yoloDetect') && !cvBtns.includes('yoloSemantic'));
  doc.querySelector('[data-auto="mazeExit"]').click();
  assert.deepStrictEqual(lastCmd(ws, 'auto').params, { func: 'mazeExit' });
  doc.querySelector('[data-cv="yoloSeg"]').click();
  assert.deepStrictEqual(lastCmd(ws, 'cv_mode').params, { mode: 'yoloSeg' });
});

test('рука: HSV-ползунки не нужны (YOLO-pose), у «Цвет» — остаются', { skip: !JSDOM }, () => {
  const { doc } = boot();
  const row = doc.getElementById('hand-color-row');
  doc.querySelector('[data-cv="findColor"]').click();
  assert.notStrictEqual(row.style.display, 'none');
  doc.querySelector('[data-cv="trackHand"]').click();
  assert.strictEqual(row.style.display, 'none');
});

test('радар виден в режимах radarScan и mazeExit, скрыт в остальных', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  const box = doc.getElementById('radar-box');
  for (const [mode, shown] of [['radarScan', 'block'], ['mazeExit', 'block'],
                               ['trackLine', 'none'], ['none', 'none']]) {
    ws.msg({ type: 'status', delta: false, data: { auto_mode: mode, radar_data: [], radar_sector: 75 } });
    assert.strictEqual(box.style.display, shown, mode);
  }
});

test('карточка Missions находится в левой колонке', { skip: !JSDOM }, () => {
  const { doc } = boot();
  const left = doc.querySelector('.col-left');
  const right = doc.querySelector('.col-right');
  assert.ok(left.contains(doc.getElementById('card-missions')), 'Missions должна быть слева');
  assert.ok(!right.contains(doc.getElementById('card-missions')));
});

test('лимиты: CAM TILT 60-170, CRANE TILT 100-150 (до прихода лимитов с сервера)', { skip: !JSDOM }, () => {
  const { w } = boot();
  const lim = (id) => JSON.parse(w.eval(`JSON.stringify((function(d){return {min:d.min,max:d.max}})(servoDefById(${id})))`));
  assert.deepStrictEqual(lim(2), { min: 60, max: 170 });
  assert.deepStrictEqual(lim(5), { min: 100, max: 150 });
  // степпер не пропускает угол за лимит
  w.eval('sendServoAngle(2, 10)');
  assert.strictEqual(w.eval('servoDefById(2).cur'), 60);
  w.eval('sendServoAngle(5, 200)');
  assert.strictEqual(w.eval('servoDefById(5).cur'), 150);
});

const servoCmds = (ws, id) => ws.sent.filter((m) => m.cmd === 'servo' && m.params.id === id);

test('джойстик камеры: шаг 5° за тик, а не мгновенный прыжок', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  w.eval('servoDefById(2).cur = 90; servoDefById(1).cur = 90; camStickVec = {x: 0, y: -1};');
  w.eval('camStepOnce()');
  assert.deepStrictEqual(servoCmds(ws, 2).map((m) => m.params.angle), [95]);   // вверх = +5
  w.eval('camLastSent.tilt = 0; camStepOnce()');
  assert.deepStrictEqual(servoCmds(ws, 2).map((m) => m.params.angle), [95, 100]);
  // вправо = pan -5; по диагонали двигаются обе оси
  w.eval('camStickVec = {x: 1, y: 1}; camLastSent.pan = 0; camLastSent.tilt = 0; camStepOnce()');
  assert.strictEqual(servoCmds(ws, 1).slice(-1)[0].params.angle, 85);
  assert.strictEqual(servoCmds(ws, 2).slice(-1)[0].params.angle, 95);          // 100 - 5
});

test('джойстик камеры: мёртвая зона не двигает камеру', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  w.eval('servoDefById(2).cur = 90; camStickVec = {x: 0.2, y: -0.3}; camStepOnce()');
  assert.strictEqual(servoCmds(ws, 2).length + servoCmds(ws, 1).length, 0);
});

test('джойстик камеры: шаги упираются в лимиты (tilt 170, pan 0/180)', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  w.eval('servoDefById(2).cur = 168; camStickVec = {x: 0, y: -1}; camStepOnce()');
  assert.strictEqual(servoCmds(ws, 2).slice(-1)[0].params.angle, 170);        // не 173
  const n = servoCmds(ws, 2).length;
  w.eval('camLastSent.tilt = 0; camStepOnce()');
  assert.strictEqual(servoCmds(ws, 2).length, n, 'за лимитом команда не уходит');
  w.eval('servoDefById(2).cur = 62; camStickVec = {x: 0, y: 1}; camLastSent.tilt = 0; camStepOnce()');
  assert.strictEqual(servoCmds(ws, 2).slice(-1)[0].params.angle, 60);         // нижний лимит
});

test('джойстик камеры: учитывает лимиты, пришедшие с сервера', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  w.eval('applyServoLimits({"2": {min: 80, max: 120}})');
  w.eval('servoDefById(2).cur = 118; camStickVec = {x: 0, y: -1}; camStepOnce()');
  assert.strictEqual(servoCmds(ws, 2).slice(-1)[0].params.angle, 120);
});

test('угол камеры берётся с сервера: шаги стартуют от реального положения', { skip: !JSDOM }, () => {
  const { w, ws } = boot();
  const angles = [90, 90, 90, 90, 90, 135, 90];
  angles[2] = 140;                                  // режим «Рука» увёл камеру на 140°
  ws.msg({ type: 'status', delta: false, data: { servo_angles: angles } });
  w.eval('camStickVec = {x: 0, y: 1}; camStepOnce()');                         // вниз = -5
  assert.strictEqual(servoCmds(ws, 2).slice(-1)[0].params.angle, 135);
});

test('джойстик камеры: таймер шагов запускается при отклонении и снимается при отпускании', { skip: !JSDOM }, () => {
  const { w } = boot();
  w.eval('onCamMove(0, -20, 20)');                                             // вверх, вне мёртвой зоны
  assert.strictEqual(w.eval('camStepTimer !== null'), true);
  w.eval('onCamEnd()');
  assert.strictEqual(w.eval('camStepTimer === null'), true);
  w.eval('onCamMove(1, 1, 20)');                                               // в мёртвой зоне
  assert.strictEqual(w.eval('camStepTimer === null'), true);
});

test('аварийный стоп: кнопка в Autonomous и в шапке отправляют estop', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  doc.getElementById('auto-estop').click();
  assert.ok(ws.sent.some((m) => m.cmd === 'estop'), 'auto-estop не отправил estop');
  doc.getElementById('estop-btn').click();
  assert.strictEqual(ws.sent.filter((m) => m.cmd === 'estop').length, 2);
});

const HW_ALL_OK = { motors: true, servos: true, leds: true, buzzer: true, switches: true,
  ultrasonic: true, mpu6050: true, oled: true, camera: true, autonomous: true, crane: true,
  ds4: false, ds4_found: true, missions: true, cv: true, voice: true };
const MODULE_CARDS = [
  ['card-mpu', 'mpu-missing-tag'], ['card-ds4', 'ds4-missing-tag'], ['card-missions', 'missions-missing-tag'],
  ['card-cv', 'cv-missing-tag'], ['card-autonomous', 'auto-missing-tag'],
];

test('«Module not found»: окна mpu6050/ds4/missions/cv/autonomous блокируются, если модуля нет', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  ws.msg({ type: 'status', delta: false, data: { hw: Object.assign({}, HW_ALL_OK,
    { mpu6050: false, ds4_found: false, missions: false, cv: false, autonomous: false }) } });
  for (const [card, tag] of MODULE_CARDS) {
    assert.ok(doc.getElementById(card), 'нет карточки ' + card);
    assert.ok(doc.getElementById(card).classList.contains('hw-missing'), card + ' должен быть заблокирован');
    assert.notStrictEqual(doc.getElementById(tag).style.display, 'none', 'тег не показан: ' + tag);
    assert.strictEqual(doc.getElementById(tag).textContent, 'Module not found');
  }
});

test('«Module not found»: когда модули есть, карточки доступны (DS4 не подключён ≠ модуль не найден)', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  // ds4:false (геймпад не подключён), но ds4_found:true — кнопки Bluetooth должны оставаться доступными
  ws.msg({ type: 'status', delta: false, data: { hw: HW_ALL_OK } });
  for (const [card, tag] of MODULE_CARDS) {
    assert.ok(!doc.getElementById(card).classList.contains('hw-missing'), card + ' заблокирован зря');
    assert.strictEqual(doc.getElementById(tag).style.display, 'none');
  }
});

test('«Module not found»: старый сервер без новых флагов не блокирует карточки', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  const old = Object.assign({}, HW_ALL_OK);
  delete old.ds4_found; delete old.missions; delete old.cv;
  ws.msg({ type: 'status', delta: false, data: { hw: old } });
  for (const [card] of MODULE_CARDS) {
    assert.ok(!doc.getElementById(card).classList.contains('hw-missing'), card + ' заблокирован без флага');
  }
});

test('«Module not found»: карточка снова разблокируется, когда модуль появился', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  ws.msg({ type: 'status', delta: false, data: { hw: Object.assign({}, HW_ALL_OK, { cv: false }) } });
  assert.ok(doc.getElementById('card-cv').classList.contains('hw-missing'));
  ws.msg({ type: 'status', delta: false, data: { hw: HW_ALL_OK } });
  assert.ok(!doc.getElementById('card-cv').classList.contains('hw-missing'));
});

test('ответ сервера module_not_found показывается переведённым тостом (RU и EN)', { skip: !JSDOM }, () => {
  const { w, ws, doc } = boot();
  const resp = { type: 'response', data: { ok: false, cmd: 'cv_mode', code: 'module_not_found', module: 'cv', error: 'Camera not available' } };
  ws.msg(resp);
  assert.match(doc.querySelector('.toast-container').lastChild.textContent, /Module not found: Computer Vision/);
  w.eval('setLang("ru")');
  ws.msg(resp);
  assert.match(doc.querySelector('.toast-container').lastChild.textContent, /Модуль не найден: Компьютерное зрение/);
});

test('сообщения сервера (английские) переводятся на язык интерфейса', { skip: !JSDOM }, () => {
  const { w } = boot();
  w.eval('setLang("ru")');
  assert.strictEqual(w.eval('srvMsg("Camera not available")'), 'Камера недоступна');
  assert.strictEqual(w.eval('srvMsg("Unknown mode: foo")'), 'Неизвестный режим: foo');
  assert.strictEqual(w.eval('srvMsg("Connected to DUALSHOCK 4")'), 'Подключено: DUALSHOCK 4');
  assert.match(w.eval('srvMsg("No Bluetooth adapter found. Check rfkill.")'), /Bluetooth-адаптер не найден/);
  assert.strictEqual(w.eval('srvMsg("что-то своё")'), 'что-то своё', 'неизвестное сообщение остаётся как есть');
  w.eval('setLang("en")');
  assert.strictEqual(w.eval('srvMsg("Camera not available")'), 'Camera not available');
});

test('склонение: 1 строка / 2 строки / 5 строк / 11 строк / 21 строка (RU) и line/lines (EN)', { skip: !JSDOM }, () => {
  const { w } = boot();
  w.eval('setLang("ru")');
  const ru = (n) => w.eval(`tn("console_lines", ${n})`);
  assert.deepStrictEqual([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 111].map(ru),
    ['0 строк', '1 строка', '2 строки', '4 строки', '5 строк', '11 строк', '12 строк', '14 строк', '21 строка', '22 строки', '25 строк', '111 строк']);
  assert.strictEqual(w.eval('tn("mission_steps", 3, {s: "Квадрат", sec: 9})'), 'Квадрат (3 шага, ~9 с)');
  assert.strictEqual(w.eval('tn("mission_steps", 8, {s: "Квадрат", sec: 12})'), 'Квадрат (8 шагов, ~12 с)');
  w.eval('setLang("en")');
  assert.strictEqual(w.eval('tn("console_lines", 1)'), '1 line');
  assert.strictEqual(w.eval('tn("console_lines", 2)'), '2 lines');
});

test('перевод: HSV-подписи, цвета LED, aria-label и фильтры логов меняются вместе с языком', { skip: !JSDOM }, () => {
  const { w, doc } = boot();
  const prev = (id) => doc.getElementById(id).previousElementSibling.textContent;
  w.eval('setLang("en")');
  assert.strictEqual(prev('hand-h-low'), 'H low');
  assert.strictEqual(doc.querySelector('.color-preset[data-hex="#ff0000"]').title, 'Red');
  assert.strictEqual(doc.getElementById('upd-file').getAttribute('aria-label'), 'Update archive');
  assert.strictEqual(doc.querySelector('.log-filter-warn').title, 'Warnings');
  w.eval('setLang("ru")');
  assert.strictEqual(prev('hand-h-low'), 'H низ');
  assert.strictEqual(prev('hand-v-high'), 'V верх');
  assert.strictEqual(doc.querySelector('.color-preset[data-hex="#ff0000"]').title, 'Красный');
  assert.strictEqual(doc.getElementById('upd-file').getAttribute('aria-label'), 'Архив обновления');
  assert.strictEqual(doc.getElementById('log-sort-select').title, 'Порядок сортировки');
  assert.strictEqual(doc.querySelector('.tab-btn[data-tab="console"] span').textContent, 'Консоль');
  assert.strictEqual(doc.querySelector('.tab-btn svg') !== null, true, 'иконка вкладки пропала при переводе');
});

test('заглушка видео переведена и не требует inline onerror (CSP)', { skip: !JSDOM }, () => {
  const { w, doc } = boot();
  assert.ok(!doc.getElementById('camera-feed').hasAttribute('onerror'), 'inline onerror блокируется CSP');
  w.eval('setLang("ru")');
  assert.match(decodeURIComponent(w.eval('feedPlaceholder("no_camera_feed")')), /Нет изображения с камеры/);
  assert.match(decodeURIComponent(w.eval('feedOff()')), /Видео на паузе/);
  w.eval('setLang("en")');
  assert.match(decodeURIComponent(w.eval('feedOff()')), /Video paused/);
});

test('тема: кнопка переключает светлую/тёмную, подпись кнопки переводится', { skip: !JSDOM }, () => {
  const { w, doc } = boot();
  const btn = doc.getElementById('theme-btn');
  assert.ok(btn, 'нет кнопки темы');
  const html = doc.documentElement;
  assert.strictEqual(html.getAttribute('data-theme') || 'light', 'light');
  btn.click();
  assert.strictEqual(html.getAttribute('data-theme'), 'dark');
  assert.match(btn.title, /light/i);
  w.eval('setLang("ru")');
  assert.match(btn.title, /светлую/i);
  btn.click();
  assert.strictEqual(html.getAttribute('data-theme'), 'light');
});

test('theme.js: по умолчанию системная тема, ручной выбор запоминается', { skip: !JSDOM }, () => {
  const dom = new JSDOM('<!DOCTYPE html><html><head><meta name="theme-color" content="#fff"></head><body></body></html>',
    { url: 'http://pi.local/', runScripts: 'outside-only' });
  const w = dom.window;
  w.eval(fs.readFileSync(path.join(STATIC, 'theme.js'), 'utf8'));
  const el = w.document.documentElement;
  assert.strictEqual(w.PicarTheme.get(), 'light');
  assert.strictEqual(el.getAttribute('data-theme'), 'light');
  assert.strictEqual(w.PicarTheme.toggle(), 'dark');
  assert.strictEqual(w.localStorage.getItem('picarpro_theme'), 'dark');
  assert.notStrictEqual(w.document.querySelector('meta[name=theme-color]').getAttribute('content'), '#fff');
  assert.strictEqual(w.PicarTheme.set('bogus'), 'dark', 'мусорное значение игнорируется');
  // «обновили страницу»: тема читается из localStorage
  w.eval(fs.readFileSync(path.join(STATIC, 'theme.js'), 'utf8'));
  assert.strictEqual(el.getAttribute('data-theme'), 'dark');
});

test('полноэкранный режим: камера переносится в оверлей, джойстики работают, выход возвращает', { skip: !JSDOM }, () => {
  const { w, ws, doc } = boot();
  const camWrap = doc.querySelector('#card-camera .camera-wrap');
  doc.getElementById('fs-enter-btn').click();
  assert.ok(doc.body.classList.contains('drive-mode'), 'body не в drive-mode');
  assert.strictEqual(doc.getElementById('drive-stage').querySelector('.camera-wrap'), camWrap,
    'видео не перенесено в оверлей');
  // джойстик движения в оверлее управляет колёсами
  const fsWheels = doc.getElementById('fs-joystick-container');
  pointer(w, fsWheels, 'pointerdown', 1, 70, 10);
  assert.strictEqual(moves(ws).pop(), 'forward');
  pointer(w, fsWheels, 'pointerup', 1, 70, 10);
  // выход возвращает видео в карточку
  doc.getElementById('drive-exit').click();
  assert.ok(!doc.body.classList.contains('drive-mode'));
  assert.strictEqual(doc.getElementById('camera-body').querySelector('.camera-wrap'), camWrap);
});

test('мини статус-бар: быстрые кнопки фар/габаритов/гудка/клешни отправляют команды', { skip: !JSDOM }, () => {
  const { ws, doc } = boot();
  doc.getElementById('fs-hl-main').click();
  assert.deepStrictEqual(lastCmd(ws, 'headlight').params, { action: 'on' });
  doc.getElementById('fs-hl-side').click();
  assert.deepStrictEqual(lastCmd(ws, 'side_lights').params, { action: 'on' });
  doc.getElementById('fs-buzzer').click();
  assert.deepStrictEqual(lastCmd(ws, 'buzzer').params, { melody: 'beep' });
  doc.getElementById('fs-crane-arm').click();
  assert.deepStrictEqual(lastCmd(ws, 'crane').params, { action: 'arm_close' });
  // статус с сервера синхронизирует обе кнопки (карточка + quickbar)
  ws.msg({ type: 'status', delta: false, data: { headlight: true, side_lights: true } });
  assert.ok(doc.getElementById('fs-hl-main').classList.contains('on'));
  assert.ok(doc.getElementById('fs-hl-side').classList.contains('on'));
});

