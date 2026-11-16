// getElementById вызывается сотни раз за обновление статуса — кэшируем узлы
(function () {
  var cache = {}, orig = document.getElementById.bind(document);
  document.getElementById = function (id) {
    var el = cache[id];
    if (el && el.isConnected) return el;
    el = orig(id);
    if (el) cache[id] = el;
    return el;
  };
})();

// ===================== i18n (v1.6.8) =====================
// Русский — язык по умолчанию, английский — дополнительный.
// Словари лежат отдельными файлами: translate/ru.js и translate/en.js.
var I18N = {
  ru: window.I18N_RU || {},
  en: window.I18N_EN || {},
};
var LANG = 'ru';
try {
  var savedLang = localStorage.getItem('picarpro_lang');
  if (savedLang === 'en' || savedLang === 'ru') LANG = savedLang;
} catch (e) {}

// t('key') -> строка; t('key', {n: 5}) подставляет {n}, {s}, {v} и т.п.
function t(key, params) {
  var dict = I18N[LANG] || I18N.ru;
  var s = dict[key];
  if (s === undefined || s === null) s = I18N.en[key];
  if (s === undefined || s === null) s = key;
  if (params) {
    Object.keys(params).forEach(function (k) {
      s = s.split('{' + k + '}').join(String(params[k]));
    });
  }
  return s;
}

function applyI18n() {
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
    el.title = t(el.dataset.i18nTitle);
  });
  updateDynamicLabels();
  buildServoGrid();   // имена серво переведённые
}

// Динамические надписи, которые JS расставляет по ходу работы
function updateDynamicLabels() {
  var jl = document.getElementById('joystick-label');
  var fjl = document.getElementById('fs-joystick-label');
  var defWheels = t('joy_wheels_default');
  if (jl && !(wheelsStick && wheelsStick.active())) jl.textContent = defWheels;
  if (fjl && !(fsWheelsStick && fsWheelsStick.active())) fjl.textContent = defWheels;
  var cl = document.getElementById('cam-joystick-label');
  var fcl = document.getElementById('fs-cam-joystick-label');
  var defCam = t('joy_cam_default');
  if (cl && !(camStick && camStick.active())) cl.textContent = defCam;
  if (fcl && !(fsCamStick && fsCamStick.active())) fcl.textContent = defCam;
  setDriver(currentDriver);
  updateCraneArmUI();
  updateCraneGripUI();
  setPortedMode('push', portedModes.push.active);
  var lStat = document.getElementById('blinker-left-status');
  if (lStat && hlLeftSignal !== undefined) refreshBlinkerText('left');
  var rStat = document.getElementById('blinker-right-status');
  if (rStat && hlRightSignal !== undefined) refreshBlinkerText('right');
  var countEl = document.getElementById('console-line-count');
  if (countEl) {
    var out = document.getElementById('console-output');
    var n = out ? out.children.length : 0;
    countEl.textContent = n === 1 ? t('console_lines_one') : t('console_lines_fmt', {n: n});
  }
}

function setLang(l) {
  LANG = l;
  try { localStorage.setItem('picarpro_lang', l); } catch (e) {}
  var ruBtn = document.getElementById('lang-ru');
  var enBtn = document.getElementById('lang-en');
  if (ruBtn) ruBtn.classList.toggle('active', l === 'ru');
  if (enBtn) enBtn.classList.toggle('active', l === 'en');
  applyI18n();
}

// Идентификатор этого устройства (вкладки) для арбитража управления
var CLIENT_ID = (function () {
  var k = 'picarpro_cid';
  try {
    var v = sessionStorage.getItem(k);
    if (!v) { v = PanelCore.makeClientId(); sessionStorage.setItem(k, v); }
    return v;
  } catch (e) { return PanelCore.makeClientId(); }
})();
var currentDriver = null;
var wsAttempt = 0;
var statusPending = null;   // статус, накопленный пока вкладка скрыта

// v1.6.8: каналы 3 и 4 убраны — на этой сборке не используются
// (канал 4 — локоть/резерв, канал 3 — резерв; см. config.py).
var servoDefs = [
  { id: 0, nameKey: 'servo_0', min: 30, max: 150, init: 90 },
  { id: 1, nameKey: 'servo_1', min: 0,  max: 180, init: 90 },
  { id: 2, nameKey: 'servo_2', min: 80, max: 170, init: 90 },
  // Клешня: наклон — канал 5, захват — канал 6.
  { id: 5, nameKey: 'servo_5', min: 0,   max: 180, init: 180 },
  { id: 6, nameKey: 'servo_6', min: 100, max: 180, init: 100 },
];
// Лимиты синхронизируются с сервера (config.py SERVO_LIMITS) — только чтение.

var hlMainOn = false;
var hlSideOn = false;
var hlLeftSignal = false;
var hlRightSignal = false;
var currentLedMode = 'off';
var lastSentDir = 'stop';
var moveThrottle = 0;
var craneArmClosed = false;
var craneGripPosition = 'mid';   // v1.6.4: high убран
var logCounts = { info: 0, warn: 0, error: 0, debug: 0 };
var logFilters = { info: true, warn: true, error: true, debug: true };
var logSortMode = 'time';
var consoleAutoScroll = true;
var hw = {
  motors: false, servos: false, leds: false, buzzer: false,
  switches: false, ultrasonic: false, mpu6050: false,
  oled: false, camera: false, autonomous: false, crane: false,
  ds4: false, voice: false,
};
var ws = null;
var wsReconnectTimer = null;
var usePolling = false;
var pollTimer = null;

// ---- токен-авторизация (если задана на сервере) ----
function getToken() {
  return localStorage.getItem('picarpro_token') || '';
}
function setToken(t_) {
  if (t_) localStorage.setItem('picarpro_token', t_);
  else localStorage.removeItem('picarpro_token');
}
function authHeaders() {
  var h = {'Content-Type': 'application/json', 'X-Client-Id': CLIENT_ID};
  var tok = getToken();
  if (tok) h['X-Auth-Token'] = tok;
  return h;
}
function handleUnauthorized() {
  var tok = prompt(t('auth_prompt'));
  if (tok !== null) { setToken(tok.trim()); location.reload(); }
}

function toast(msg, type) {
  type = type || 'info';
  var container = document.getElementById('toast-container');
  var el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(function() {
    el.style.animation = 'toast-out .3s ease forwards';
    setTimeout(function() { el.remove(); }, 300);
  }, 3000);
}

function hexToRgb(hex) {
  return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
}

var lastWebActive = 0;
function sendCommand(cmd, params) {
  params = params || {};
  if (cmd === 'move' || cmd === 'speed') {
    // web_active — не чаще раза в 10 с (раньше улетал с КАЖДЫМ move:
    // двойной спам по WS при драге джойстика)
    var nowWa = Date.now();
    if (nowWa - lastWebActive > 10000) {
      lastWebActive = nowWa;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({cmd: 'web_active', params: {active: true}}));
      }
    }
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd: cmd, params: params}));
  } else {
    var urlMap = {
      'move': '/cmd/move', 'speed': '/cmd/speed', 'servo': '/cmd/servo',
      'led': '/cmd/led', 'buzzer': '/cmd/buzzer',
      'buzzer_stop': '/cmd/buzzer_stop',
      'cv_mode': '/cmd/cv_mode', 'auto': '/cmd/auto', 'crane': '/cmd/crane',
      'voice': '/cmd/voice', 'headlight': '/cmd/headlight', 'blinker': '/cmd/blinker',
      'side_lights': '/cmd/side_lights',
      'hand_color': '/cmd/hand_color', 'servo_get_limits': '/cmd/servo_get_limits',
      'i2c_scan': '/cmd/i2c_scan',
      'web_active': '/cmd/web_active', 'clear_log': '/cmd/clear_log',
      'steady': '/cmd/steady', 'battery': '/cmd/battery',
      'push': '/cmd/push', 'yolo_status': '/cmd/yolo_status',
      'mission': '/cmd/mission', 'estop': '/cmd/estop', 'take_control': '/cmd/take_control',
      'release_control': '/cmd/release_control',
    };
    var url = urlMap[cmd];
    if (url) {
      fetch(url, { method: 'POST', headers: authHeaders(), body: JSON.stringify(params) })
        .then(function(r) {
          if (r.status === 401) { handleUnauthorized(); return null; }
          return r.json();
        })
        .then(function(m) { handleResponse(m); })
        .catch(function() {});
    }
  }
}

document.querySelectorAll('.collapsible-header').forEach(function(header) {
  header.addEventListener('click', function() {
    var targetId = header.dataset.target;
    if (!targetId) return;
    var body = document.getElementById(targetId);
    if (!body) return;
    header.classList.toggle('open');
    body.classList.toggle('open');
    saveCollapsed();
  });
});

// Состояние свёрнутых блоков помним (на телефоне иначе каждый раз листать
// видео, режимы и геймпад до нужного).
var COLLAPSE_KEY = 'picarpro_collapsed';
function saveCollapsed() {
  try {
    var closed = [];
    document.querySelectorAll('.collapsible-header[data-target]').forEach(function(h) {
      if (!h.classList.contains('open')) closed.push(h.dataset.target);
    });
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(closed));
  } catch (e) {}
}
function restoreCollapsed() {
  var closed = null;
  try { closed = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || 'null'); } catch (e) {}
  if (closed === null && window.innerWidth <= 700) {
    // первый запуск на телефоне: оставляем видео, режимы и движение,
    // остальное свёрнуто
    closed = ['ds4-body', 'voice-body', 'servo-body', 'mpu-body', 'hl-body',
              'led-body', 'buzzer-body', 'cv-body'];
  }
  (closed || []).forEach(function(id) {
    var body = document.getElementById(id);
    var head = document.querySelector('.collapsible-header[data-target="' + id + '"]');
    if (body && head) { body.classList.remove('open'); head.classList.remove('open'); }
  });
}

document.querySelectorAll('.tab-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    document.getElementById('content-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'console') {
      // очистка + перезагрузка истории: DOM консоли не растёт бесконечно
      var out = document.getElementById('console-output');
      if (out) out.innerHTML = '';
      fetchConsoleHistory();
    }
    updateVideoFeed();
  });
});

// ---- видео: пауза MJPEG-потока, когда он не виден (главный тормоз
// телефонов — декодирование 15 fps JPEG в фоне) --------------------
var FEED_URL = '/video_feed';
var FEED_OFF = "data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22640%22 height=%22480%22><rect fill=%22%231a1a1a%22 width=%22640%22 height=%22480%22/><text x=%2250%25%22 y=%2250%25%22 fill=%22%235f6368%22 font-size=%2220%22 text-anchor=%22middle%22 dy=%22.3em%22>Video paused</text></svg>";
function updateVideoFeed() {
  var img = document.getElementById('camera-feed');
  if (!img) return;
  var controlTab = document.getElementById('content-control');
  // в полноэкранном режиме поток нужен всегда (не только на вкладке Control)
  var driveMode = document.body.classList.contains('drive-mode');
  var visible = (controlTab && controlTab.classList.contains('active') && !document.hidden) || driveMode;
  var want = visible ? FEED_URL : FEED_OFF;
  if (img.dataset.feedState !== want) {
    img.dataset.feedState = want;
    img.src = want;
  }
}
document.addEventListener('visibilitychange', updateVideoFeed);

function toggleHwSection(cardId, tagId, available) {
  var tagEl = document.getElementById(tagId);
  if (!tagEl) return;
  if (available) {
    tagEl.style.display = 'none';
    if (cardId) {
      var card = document.getElementById(cardId);
      if (card) card.classList.remove('hw-missing');
    }
    if (!cardId) {
      var parentCard = tagEl.closest('.card');
      if (parentCard) parentCard.classList.remove('hw-missing');
    }
  } else {
    tagEl.style.display = '';
    if (cardId) {
      var card = document.getElementById(cardId);
      if (card) card.classList.add('hw-missing');
    }
    if (!cardId) {
      var parentCard = tagEl.closest('.card');
      if (parentCard) parentCard.classList.add('hw-missing');
    }
  }
}

// v1.6.8: окна mpu6050, ds4, missions, cv и autonomous — ВСЕГДА активны,
// без проверки «Module not found»: их отсутствие не блокирует интерфейс
// (датчики просто показывают N/A, режимы сообщат об ошибке при запуске).
// Проверка осталась только для реально опциональных модулей.
function updateHardwareUI(hardwareStatus) {
  if (!hardwareStatus) return;
  hw = hardwareStatus;
  toggleHwSection(null, 'servo-missing-tag', hw.servos);
  toggleHwSection('card-headlights', 'hl-missing-tag', hw.switches);
  toggleHwSection('card-led', 'led-missing-tag', hw.leds);
  toggleHwSection('card-buzzer', 'buzzer-missing-tag', hw.buzzer);
  toggleHwSection(null, 'claw-missing-tag', hw.crane);
  toggleHwSection('card-voice', 'voice-missing-tag', hw.voice);
}

var lastNotDriverToast = 0;
function setDriver(driver) {
  currentDriver = driver || null;
  var st = PanelCore.driverState(currentDriver, CLIENT_ID);
  var chip = document.getElementById('driver-chip');
  if (chip) {
    chip.dataset.state = st;
    chip.textContent = st === 'you' ? t('driver_chip_you')
      : (st === 'other' ? t('driver_chip_other') : t('driver_chip_free'));
  }
  document.body.classList.toggle('not-driver', st === 'other');
}

function handleResponse(m) {
  if (!m) return;
  if (m.driver !== undefined) setDriver(m.driver);
  if (m.missions) renderMissionList(m.missions);
  if (m.cmd === 'mission' && m.ok !== false) {
    renderMissionStatus(m);
    if (m.action === 'save' || m.action === 'delete' || m.saved) refreshMissions();
  }
  if (m.code === 'not_driver') {
    var now = Date.now();
    if (now - lastNotDriverToast > 3000) {   // не спамим при драге джойстика
      lastNotDriverToast = now;
      toast(t('toast_not_driver'), 'error');
    }
    return;
  }
  if (m.error) toast(m.error, 'error');
  if (m.interrupted_auto) toast(t('toast_interrupted'), 'info');
  if (m.cmd === 'servo_get_limits' && m.ok) applyServoLimits(m.limits);
}

// Статус: пока вкладка скрыта — копим, применяем одним разом при возврате
function onStatus(data, isDelta) {
  if (document.hidden) {
    statusPending = PanelCore.mergeStatus(statusPending, data, isDelta && !!statusPending);
    return;
  }
  updateStatus(data);
}
function flushPendingStatus() {
  if (statusPending && !document.hidden) {
    var d = statusPending; statusPending = null;
    updateStatus(d);
  }
}

function isPanelVisible() {
  var c = document.getElementById('content-control');
  return !document.hidden && !!c && c.classList.contains('active');
}
var lastReportedVisible = null;
function reportView() {
  var v = isPanelVisible();
  if (v === lastReportedVisible) return;
  lastReportedVisible = v;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd: 'view', params: {visible: v}}));
  }
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  var delay = PanelCore.reconnectDelay(wsAttempt++);
  wsReconnectTimer = setTimeout(function() { wsReconnectTimer = null; wsConnect(); }, delay);
}
function reconnectNow() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  wsConnect();
}
window.addEventListener('online', reconnectNow);

function wsConnect() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  var wsHost = location.hostname;
  var wsPort = 8888;
  var token = getToken();
  var url = 'ws://' + wsHost + ':' + wsPort + (token ? ('/?token=' + encodeURIComponent(token)) : '');
  try {
    ws = new WebSocket(url);
    ws.onopen = function() {
      wsAttempt = 0;
      document.getElementById('connection-dot').classList.remove('offline');
      usePolling = false;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      lastReportedVisible = null;
      // представляемся: id устройства для арбитража + видима ли панель
      ws.send(JSON.stringify({cmd: 'hello', params: {id: CLIENT_ID, visible: isPanelVisible()}}));
      lastReportedVisible = isPanelVisible();
      refreshMissions();
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        var msgType = data.type || '';
        var msgData = data.data || {};
        if (msgType === 'auth_required') {
          handleUnauthorized();
          ws.close();
        }
        else if (msgType === 'status') onStatus(msgData, !!data.delta);
        else if (msgType === 'log') appendConsoleLine(data.text, data.ts);
        else if (msgType === 'log_history') {
          if (data.lines && data.lines.length) {
            data.lines.forEach(function(item) { appendConsoleLine(item[1], item[0]); });
          }
        }
        else if (msgType === 'response') handleResponse(msgData);
      } catch(err) {}
    };
    ws.onclose = function() {
      document.getElementById('connection-dot').classList.add('offline');
      // экспоненциальная пауза вместо фиксированных 3 с
      scheduleReconnect();
      if (!usePolling) { usePolling = true; startPolling(); }
    };
    ws.onerror = function() { ws.close(); };
  } catch(e) { usePolling = true; startPolling(); scheduleReconnect(); }
}

function startPolling() {
  if (pollTimer) return;
  function poll() {
    fetch('/api/status', { headers: (function(){ var h = {}; var tok = getToken(); if (tok) h['X-Auth-Token'] = tok; return h; })() })
      .then(function(r) {
        if (r.status === 401) { handleUnauthorized(); throw new Error('unauthorized'); }
        return r.json();
      })
      .then(function(d) { updateStatus(d); })
      .catch(function() {});
  }
  poll();
  pollTimer = setInterval(poll, 1500);
}

function fetchConsoleHistory() {
  fetch('/api/logs', { headers: (function(){ var h = {}; var tok = getToken(); if (tok) h['X-Auth-Token'] = tok; return h; })() })
    .then(function(r) { return r.json(); }).then(function(d) {
    if (d.lines) {
      // только последние 250 строк — на телефоне 800 DOM-узлов тормозят
      d.lines.slice(-CONSOLE_HISTORY_LINES)
        .forEach(function(item) { appendConsoleLine(item[1], item[0]); });
    }
  }).catch(function() {});
}

var firstStatus = true;
// v1.6.2: радар как в оригинальном приложении Adeept — сектор скана с
// точками препятствий; точки приходят в статусе (radar_data).
// v1.6.5: «нет эха» сервер шлёт как RADAR_MAX_CM (свободно) — рисуем такие
// точки тускло-зелёными, препятствия — красными; при недоступном HC-SR04
// выводим сообщение вместо пустого экрана.
var RADAR_MAX_CM = 200;   // ULTRASONIC_MAX_DISTANCE (м) * 100
var RADAR_SECTOR_DEG = 60;   // сектор скана: ±60° (см. _radar_scan)
var currentAutoMode = 'none';
function drawRadar(points, offline) {
  var cv = document.getElementById('radar-canvas');
  if (!cv || !cv.getContext) return;
  var ctx = cv.getContext('2d');
  var W = cv.width, H = cv.height;
  var cx = W / 2, cy = H - 20, R = H - 40;
  var sector = RADAR_SECTOR_DEG * Math.PI / 180;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0b1a10';
  ctx.fillRect(0, 0, W, H);
  // дистанционные дуги 50/100/150/200 см
  ctx.strokeStyle = 'rgba(80,200,120,0.35)';
  ctx.lineWidth = 1;
  [50, 100, 150, 200].forEach(function(cm) {
    ctx.beginPath();
    ctx.arc(cx, cy, R * cm / RADAR_MAX_CM, -Math.PI / 2 - sector, -Math.PI / 2 + sector);
    ctx.stroke();
  });
  // границы сектора и центральный луч
  [-1, 0, 1].forEach(function(k) {
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.sin(k * sector) * R, cy - Math.cos(k * sector) * R);
    ctx.strokeStyle = k === 0 ? 'rgba(80,200,120,0.5)' : 'rgba(80,200,120,0.35)';
    ctx.stroke();
  });
  // точки, увиденные ультразвуком: препятствия — красные, «свободно» —
  // тускло-зелёные у внешней дуги
  (points || []).forEach(function(p) {
    if (p.distance === null || p.distance === undefined || p.distance <= 0) return;
    var d = Math.min(p.distance, RADAR_MAX_CM);
    var a = (p.angle || 0) * Math.PI / 180;
    var x = cx + Math.sin(a) * R * d / RADAR_MAX_CM;
    var y = cy - Math.cos(a) * R * d / RADAR_MAX_CM;
    var free = p.distance >= RADAR_MAX_CM - 2;
    ctx.fillStyle = free ? 'rgba(80,200,120,0.45)' : '#ff5252';
    ctx.beginPath();
    ctx.arc(x, y, free ? 2 : 3.5, 0, Math.PI * 2);
    ctx.fill();
  });
  // корпус робота
  ctx.fillStyle = '#4caf50';
  ctx.fillRect(cx - 5, cy - 3, 10, 6);
  if (offline) {
    ctx.fillStyle = '#ffb74d';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(t('radar_offline'), cx, 16);
  }
}

// Подпись активного CV-режима на бейдже поверх видео
var CV_LABEL_KEYS = {
  findlineCV: 'cv_label_line', trackHand: 'cv_label_hand',
  findColor: 'cv_label_color', watchDog: 'cv_label_watchdog',
  yoloDetect: 'cv_label_yolo', yoloSeg: 'cv_label_yolo_seg',
  yoloSemantic: 'cv_label_yolo_sem',
};
function setCvBadge(mode) {
  var badge = document.getElementById('cv-badge');
  if (!badge) return;
  if (mode && mode !== 'none') {
    var key = CV_LABEL_KEYS[mode];
    badge.textContent = t('cv_badge_prefix') + (key ? t(key) : mode);
    badge.classList.add('visible');
  } else {
    badge.classList.remove('visible');
  }
}

function updateStatus(d) {
  if (!d) return;
  if ('driver' in d) setDriver(d.driver);
  if (d.mission) renderMissionStatus(d.mission);
  if (d.clients !== undefined) document.getElementById('sb-clients').textContent = d.clients;
  if (d.cpu_temp !== undefined) document.getElementById('sb-cpu-temp').textContent = d.cpu_temp + '\u00B0C';
  if (d.cpu_usage !== undefined) document.getElementById('sb-cpu-usage').textContent = d.cpu_usage + '%';
  if (d.ram_percent !== undefined || d.ram) {
    var ramText;
    if (d.ram && d.ram.used_mb !== undefined) {
      ramText = d.ram.used_mb + '/' + d.ram.total_mb + 'M ' + d.ram.percent + '%';
    } else {
      ramText = d.ram_percent + '%';
    }
    document.getElementById('sb-ram').textContent = ramText;
  }
  if (d.distance !== undefined) document.getElementById('sb-distance').textContent = d.distance + 'cm';
  // v1.6.5: скорость в панели — фактическая: удержание L2/R2 (турбо) = 100 %,
  // геймпад без турбо = его текущая скорость хода, иначе ползунок веб-панели
  var spdEl = document.getElementById('sb-speed');
  var pad = d.ds4;
  if (pad && pad.connected && pad.turbo) {
    spdEl.textContent = '100%';
    spdEl.classList.add('turbo');
  } else if (pad && pad.connected && pad.drive_speed > 0) {
    spdEl.textContent = pad.drive_speed + '%';
    spdEl.classList.remove('turbo');
  } else if (d.speed !== undefined) {
    spdEl.textContent = d.speed + '%';
    spdEl.classList.remove('turbo');
  }
  if (d.auto_mode !== undefined) currentAutoMode = d.auto_mode;
  var radarBox = document.getElementById('radar-box');
  if (radarBox) radarBox.style.display = currentAutoMode === 'radarScan' ? 'block' : 'none';
  if (d.radar_data) drawRadar(d.radar_data, d.ultra_ok === false);
  else if (currentAutoMode === 'radarScan') drawRadar([], d.ultra_ok === false);
  if (d.hw) {
    updateHardwareUI(d.hw);
    if (firstStatus) { firstStatus = false; }
  }
  if (d.servo_limits) {
    applyServoLimits(d.servo_limits);
  }
  // синхронизация углов с сервера (когда поле не в фокусе)
  if (d.servo_angles && Array.isArray(d.servo_angles)) {
    d.servo_angles.forEach(function(ang, sid) {
      var inp = document.querySelector('input.servo-angle-input[data-servo="' + sid + '"]');
      if (inp && document.activeElement !== inp) {
        var def = servoDefById(sid);
        if (def && Math.abs((def.cur || 0) - ang) >= 1) {
          def.cur = ang;
          inp.value = ang;
          var val = document.querySelector('label .val[data-servo-val="' + sid + '"]');
          if (val) val.textContent = ang + '\u00B0';
        }
      }
    });
  }
  // статус Push Mode (толчок препятствий клешнёй по HC-SR04)
  if (d.push && d.push.active !== undefined) {
    setPortedMode('push', d.push.active);
  }
  var mpu = d.mpu6050;
  if (!('mpu6050' in d)) {
    // поле не менялось — оставляем показания как есть
  } else if (mpu) {
    document.getElementById('sb-imu').textContent = 'R:' + mpu.roll + '\u00B0 P:' + mpu.pitch + '\u00B0';
    document.getElementById('mpu-ax').textContent = mpu.accel.x.toFixed(3);
    document.getElementById('mpu-ay').textContent = mpu.accel.y.toFixed(3);
    document.getElementById('mpu-az').textContent = mpu.accel.z.toFixed(3);
    document.getElementById('mpu-gx').textContent = mpu.gyro.x.toFixed(1);
    document.getElementById('mpu-gy').textContent = mpu.gyro.y.toFixed(1);
    document.getElementById('mpu-gz').textContent = mpu.gyro.z.toFixed(1);
    document.getElementById('mpu-roll').textContent = mpu.roll.toFixed(1);
    document.getElementById('mpu-pitch').textContent = mpu.pitch.toFixed(1);
  } else {
    document.getElementById('sb-imu').textContent = 'N/A';
  }
  if (!('ir_left' in d)) {
    // не менялось
  } else if (d.ir_left !== undefined && d.ir_left !== null) {
    setIrVal('ir-left-val', d.ir_left);
  } else {
    setIrVal('ir-left-val', null);
  }
  if (!('ir_middle' in d)) {
    // не менялось
  } else if (d.ir_middle !== undefined && d.ir_middle !== null) {
    setIrVal('ir-middle-val', d.ir_middle);
  } else {
    setIrVal('ir-middle-val', null);
  }
  if (!('ir_right' in d)) {
    // не менялось
  } else if (d.ir_right !== undefined && d.ir_right !== null) {
    setIrVal('ir-right-val', d.ir_right);
  } else {
    setIrVal('ir-right-val', null);
  }
  var lvBanner = document.getElementById('low-voltage-banner');
  if (lvBanner && 'low_voltage' in d) lvBanner.style.display = d.low_voltage ? 'block' : 'none';
  var bat = d.battery;
  var batItem = document.getElementById('sb-battery-item');
  if (batItem && 'battery' in d) {
    if (bat && bat.adc && bat.percent !== null && bat.percent !== undefined) {
      batItem.style.display = '';
      var batEl = document.getElementById('sb-battery');
      batEl.textContent = bat.percent + '%';
      batEl.style.color = bat.low ? '#ea4335' : '#34a853';
    } else {
      batItem.style.display = 'none';
    }
  }
  var steadyItem = document.getElementById('sb-steady-item');
  if (steadyItem && ('steady_active' in d || 'hw' in d)) {
    var steadyOn = !!d.steady_active;
    steadyItem.style.display = hw.mpu6050 ? '' : 'none';
    var stEl = document.getElementById('sb-steady');
    stEl.textContent = steadyOn ? t('on') : t('off');
    stEl.style.color = steadyOn ? '#34a853' : '#9aa0a6';
    var stBtn = document.getElementById('steady-toggle');
    if (stBtn) stBtn.classList.toggle('active', steadyOn);
  }
  var ds4 = d.ds4;
  if (ds4) {
    document.getElementById('sb-ds4').textContent = ds4.connected ? t('on') : '--';
    var ds4Dot = document.getElementById('ds4-status-dot');
    var ds4Text = document.getElementById('ds4-status-text');
    if (ds4Dot) ds4Dot.style.background = ds4.connected ? '#34a853' : '#ea4335';
    if (ds4Text) {
      ds4Text.textContent = ds4.connected
        ? t('ds4_connected_fmt', {n: ds4.connect_count})
        : t('ds4_not_connected');
      ds4Text.style.color = ds4.connected ? '#34a853' : '#9aa0a6';
    }
    var btDiscBtn = document.getElementById('bt-disconnect-btn');
    if (btDiscBtn) btDiscBtn.style.display = ds4.connected ? '' : 'none';
    if (ds4.crane_arm_closed !== undefined) {
      craneArmClosed = ds4.crane_arm_closed;
      updateCraneArmUI();
    }
    if (ds4.crane_grip !== undefined) {
      craneGripPosition = ds4.crane_grip;
      updateCraneGripUI();
    }
  }
  if (d.crane_arm_closed !== undefined) {
    craneArmClosed = d.crane_arm_closed;
    updateCraneArmUI();
  }
  if (d.crane_grip_position !== undefined) {
    craneGripPosition = d.crane_grip_position;
    updateCraneGripUI();
  }
  if (d.cv_mode !== undefined) {
    setCvBadge(d.cv_mode);
  }
  if (d.headlight !== undefined) {
    hlMainOn = d.headlight;
    setHeadlightUI(hlMainOn);
  }
  if (d.side_lights !== undefined) {
    hlSideOn = d.side_lights;
    setSideLightsUI(hlSideOn);
  }
  if (d.left_blinker !== undefined) {
    hlLeftSignal = d.left_blinker;
    document.getElementById('hl-left-signal').className = 'signal-btn ' + (hlLeftSignal ? 'on' : 'off');
    refreshBlinkerText('left');
    var lDot = document.getElementById('blinker-left-dot');
    if (lDot) lDot.dataset.on = hlLeftSignal ? '1' : '0';
  }
  if (d.right_blinker !== undefined) {
    hlRightSignal = d.right_blinker;
    document.getElementById('hl-right-signal').className = 'signal-btn ' + (hlRightSignal ? 'on' : 'off');
    refreshBlinkerText('right');
    var rDot = document.getElementById('blinker-right-dot');
    if (rDot) rDot.dataset.on = hlRightSignal ? '1' : '0';
  }
  if (d.voice) {
    var v = d.voice;
    var vDot = document.getElementById('voice-status-dot');
    var vText = document.getElementById('voice-status-text');
    var vStart = document.getElementById('voice-start-btn');
    var vStop = document.getElementById('voice-stop-btn');
    if (vDot) vDot.style.background = v.active ? '#34a853' : (v.available ? '#fbbc04' : '#9aa0a6');
    if (vText) {
      vText.textContent = v.active ? t('voice_listening') : (v.available ? t('voice_ready') : t('voice_inactive'));
      vText.style.color = v.active ? '#34a853' : '#9aa0a6';
    }
    if (vStart && vStop) {
      if (v.active) { vStart.style.display = 'none'; vStop.style.display = ''; }
      else { vStart.style.display = ''; vStop.style.display = 'none'; }
    }
    if (v.last_command) document.getElementById('voice-last-cmd').textContent = v.last_command;
  }
  if (d.led_mode !== undefined) {
    currentLedMode = d.led_mode;
    document.querySelectorAll('#led-group .gbtn').forEach(function(b) {
      b.classList.toggle('active', b.dataset.led === currentLedMode);
    });
  }
}

function setIrVal(id, val) {
  var el = document.getElementById(id);
  if (!el) return;
  if (val === null || val === undefined) {
    el.textContent = t('ir_na');
    el.style.color = '#9aa0a6';
  } else if (val) {
    el.textContent = t('ir_line');
    el.style.color = '#ea4335';
  } else {
    el.textContent = t('ir_clear');
    el.style.color = '#34a853';
  }
}

function refreshBlinkerText(side) {
  var statEl = document.getElementById('blinker-' + side + '-status');
  if (!statEl) return;
  var on = side === 'left' ? hlLeftSignal : hlRightSignal;
  statEl.textContent = on ? t('on') : t('off');
  statEl.style.color = on ? '#fbbc04' : '#9aa0a6';
}

// Фары/габариты: состояния синхронизируются и в карточке, и в мини-статус-баре
// полноэкранного режима
function setHeadlightUI(on) {
  var btn = document.getElementById('hl-main');
  if (btn) btn.className = 'headlight-btn ' + (on ? 'on' : 'off');
  var q = document.getElementById('fs-hl-main');
  if (q) q.className = 'quick-btn ' + (on ? 'on' : 'off');
}
function setSideLightsUI(on) {
  var btn = document.getElementById('hl-side');
  if (btn) btn.className = 'headlight-btn ' + (on ? 'on' : 'off');
  var q = document.getElementById('fs-hl-side');
  if (q) q.className = 'quick-btn ' + (on ? 'on' : 'off');
}

function toggleHeadlight() {
  hlMainOn = !hlMainOn;
  sendCommand('headlight', { action: hlMainOn ? 'on' : 'off' });
  setHeadlightUI(hlMainOn);
}
function toggleSideLights() {
  hlSideOn = !hlSideOn;
  sendCommand('side_lights', { action: hlSideOn ? 'on' : 'off' });
  setSideLightsUI(hlSideOn);
}

function updateCraneArmUI() {
  var toggleBtn = document.getElementById('crane-arm-toggle');
  if (toggleBtn) {
    if (craneArmClosed) {
      toggleBtn.classList.add('active');
      toggleBtn.textContent = t('crane_release');
    } else {
      toggleBtn.classList.remove('active');
      toggleBtn.textContent = t('crane_grab_release');
    }
  }
  // синхронная кнопка в мини-статус-баре полноэкранного режима
  var q = document.getElementById('fs-crane-arm');
  if (q) q.className = 'quick-btn ' + (craneArmClosed ? 'on' : 'off');
}

function toggleCraneArm() {
  craneArmClosed = !craneArmClosed;
  sendCommand('crane', { action: craneArmClosed ? 'arm_close' : 'arm_open' });
  updateCraneArmUI();
}

function updateCraneGripUI() {
  var lowBtn = document.getElementById('crane-grip-low');
  var midBtn = document.getElementById('crane-grip-mid');
  var label = document.getElementById('crane-grip-label');
  if (!lowBtn) return;
  lowBtn.classList.remove('active');
  midBtn.classList.remove('active');
  var angleMap = { low: 100, mid: 135 };
  var keyMap = { low: 'grip_low', mid: 'grip_mid' };
  if (craneGripPosition === 'low') lowBtn.classList.add('active');
  else if (craneGripPosition === 'mid') midBtn.classList.add('active');
  if (label && keyMap[craneGripPosition] !== undefined) {
    label.textContent = t('grip_label_fmt',
      {s: t(keyMap[craneGripPosition]), a: angleMap[craneGripPosition]});
  }
}

// ===================== Servo Control (степперы ±1°) =====================
// Степперы: точная настройка по 1 градусу с удержанием (автоповтор)
// и прямым вводом угла. Лимиты — только из config.py (SERVO_LIMITS),
// редактирование из UI удалено v1.3.0. Каналы 3 и 4 убраны (v1.6.8).

function servoDefById(id) {
  for (var i = 0; i < servoDefs.length; i++) {
    if (servoDefs[i].id === id) return servoDefs[i];
  }
  return null;
}

function clampAngle(def, angle) {
  angle = parseInt(angle, 10);
  if (isNaN(angle)) angle = def.init;
  return Math.max(def.min, Math.min(def.max, angle));
}

function sendServoAngle(id, angle) {
  var def = servoDefById(id);
  if (!def) return;
  var clamped = clampAngle(def, angle);
  def.cur = clamped;
  var inp = document.querySelector('input.servo-angle-input[data-servo="' + id + '"]');
  var val = document.querySelector('label .val[data-servo-val="' + id + '"]');
  if (inp) inp.value = clamped;
  if (val) val.textContent = clamped + '\u00B0';
  sendCommand('servo', { id: id, angle: clamped });
}

function buildServoGrid() {
  var grid = document.getElementById('servo-grid');
  if (!grid) return;
  var hadFocus = document.activeElement && document.activeElement.matches &&
                 document.activeElement.matches('input.servo-angle-input');
  if (hadFocus) return;   // не пересобираем, пока пользователь вводит угол
  grid.innerHTML = '';
  servoDefs.forEach(function(s) {
    if (s.cur === undefined) s.cur = s.init;
    var item = document.createElement('div');
    item.className = 'servo-item';
    item.innerHTML = '<label>' + t(s.nameKey) + ' <span class="val" data-servo-val="' + s.id + '">' + s.cur + '\u00B0</span></label>' +
      '<div class="servo-step-row">' +
      '<button type="button" class="servo-step-btn" data-servo-dec="' + s.id + '" title="' + t('servo_step_minus') + '">&minus;</button>' +
      '<input type="number" class="servo-angle-input" data-servo="' + s.id + '" min="' + s.min + '" max="' + s.max + '" value="' + s.cur + '">' +
      '<button type="button" class="servo-step-btn" data-servo-inc="' + s.id + '" title="' + t('servo_step_plus') + '">+</button>' +
      '</div>' +
      '<div class="servo-limits-row"><span class="servo-limit-label">' + s.min + '\u2013' + s.max + '\u00B0</span></div>';
    grid.appendChild(item);
  });

  // --- степперы: тап = ±1°, удержание = автоповтор ----------------------
  document.querySelectorAll('.servo-step-btn').forEach(function(btn) {
    var repeatTimer = null, delayTimer = null;
    function stepOnce() {
      var isInc = btn.hasAttribute('data-servo-inc');
      var id = parseInt(isInc ? btn.getAttribute('data-servo-inc')
                              : btn.getAttribute('data-servo-dec'), 10);
      var def = servoDefById(id);
      if (!def) return;
      sendServoAngle(id, def.cur + (isInc ? 1 : -1));
    }
    function stopRepeat() {
      if (delayTimer) { clearTimeout(delayTimer); delayTimer = null; }
      if (repeatTimer) { clearInterval(repeatTimer); repeatTimer = null; }
    }
    btn.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      stepOnce();
      delayTimer = setTimeout(function() {
        repeatTimer = setInterval(stepOnce, 120);   // ~8°/с при удержании
      }, 400);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(function(ev) {
      btn.addEventListener(ev, stopRepeat);
    });
    btn.addEventListener('contextmenu', function(e) { e.preventDefault(); });
  });

  // --- прямой ввод угла ---------------------------------------------------
  document.querySelectorAll('input.servo-angle-input').forEach(function(inp) {
    inp.addEventListener('change', function() {
      sendServoAngle(parseInt(inp.getAttribute('data-servo'), 10), inp.value);
    });
  });
}

function applyServoLimits(limits) {
  for (var sid in limits) {
    var lim = limits[sid];
    var id = parseInt(sid, 10);
    var def = servoDefById(id);
    if (!def) continue;
    def.min = lim.min;
    def.max = lim.max;
    if (def.cur === undefined) def.cur = def.init;
    def.cur = clampAngle(def, def.cur);
    var angleInp = document.querySelector('input.servo-angle-input[data-servo="' + sid + '"]');
    if (angleInp) {
      angleInp.min = lim.min;
      angleInp.max = lim.max;
      // не затираем значение, пока пользователь редактирует поле
      if (document.activeElement !== angleInp) angleInp.value = def.cur;
    }
    var val = document.querySelector('label .val[data-servo-val="' + sid + '"]');
    if (val) val.textContent = def.cur + '\u00B0';
  }
}

var joystickContainer = document.getElementById('joystick-container');
var joystickKnob = document.getElementById('joystick-knob');
var joystickLabel = document.getElementById('joystick-label');

// Подпись направления движения по логическому dir (dir_forward и т.д.)
function dirLabel(dir) {
  var s = t('dir_' + dir);
  return s === ('dir_' + dir) ? dir : s;
}
function setWheelLabels(txt) {
  if (joystickLabel) joystickLabel.textContent = txt;
  var fjl = document.getElementById('fs-joystick-label');
  if (fjl) fjl.textContent = txt;
}
function setCamLabels(txt) {
  if (camJoystickLabel) camJoystickLabel.textContent = txt;
  var fcl = document.getElementById('fs-cam-joystick-label');
  if (fcl) fcl.textContent = txt;
}

// Универсальный джойстик на Pointer Events: у каждого свой pointerId, поэтому
// «колёса» и «камера» работают одновременно двумя пальцами. Раньше оба
// слушали общие touchend/touchmove и брали touches[0]: отпускание одного
// пальца сбрасывало второй джойстик, а камера «читала» палец колёс.
function makeStick(container, knob, pad, onMove, onEnd) {
  if (!container || !knob) return null;
  var activeId = null, rect = null;
  function center() {
    if (!rect) {          // rect кэшируется на время драга (без reflow на каждый move)
      var b = container.getBoundingClientRect();
      rect = { x: b.left + b.width / 2, y: b.top + b.height / 2, r: b.width / 2 - pad };
    }
    return rect;
  }
  function handle(e) {
    var c = center();
    var dx = e.clientX - c.x, dy = e.clientY - c.y;
    var dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > c.r) { dx = dx / dist * c.r; dy = dy / dist * c.r; }
    knob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
    onMove(dx, dy, c.r);
  }
  function finish() {
    if (activeId === null) return;
    activeId = null;
    knob.classList.add('spring-back');
    knob.style.transform = 'translate(-50%, -50%)';
    setTimeout(function() { knob.classList.remove('spring-back'); }, 300);
    onEnd();
  }
  container.addEventListener('pointerdown', function(e) {
    if (activeId !== null) return;
    activeId = e.pointerId;
    rect = null;
    try { container.setPointerCapture(e.pointerId); } catch (err) {}
    knob.classList.remove('spring-back');
    e.preventDefault();
    handle(e);
  });
  container.addEventListener('pointermove', function(e) {
    if (e.pointerId === activeId) handle(e);
  });
  function up(e) { if (e.pointerId === activeId) finish(); }
  container.addEventListener('pointerup', up);
  container.addEventListener('pointercancel', up);
  container.addEventListener('lostpointercapture', up);
  return { release: finish, active: function() { return activeId !== null; } };
}

function sendMoveDir(dir, force) {
  var now = Date.now();
  if (dir !== lastSentDir || (force && now - moveThrottle > 150)) {
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
    moveThrottle = now;
  }
}

function onWheelsMove(dx, dy) {
  var dir = PanelCore.vectorToDir(dx, dy, 10);
  setWheelLabels(dirLabel(dir) || dir);
  sendMoveDir(dir, true);          // тот же темп повторов, что и раньше (150 мс)
}
function onWheelsEnd() {
  setWheelLabels(t('joy_wheels_default'));
  sendMoveDir('stop', false);
}

var wheelsStick = makeStick(joystickContainer, joystickKnob, 24, onWheelsMove, onWheelsEnd);

var camJoystickContainer = document.getElementById('cam-joystick-container');
var camJoystickKnob = document.getElementById('cam-joystick-knob');
var camJoystickLabel = document.getElementById('cam-joystick-label');
var camPan = 90, camTilt = 90;

function camClamp(id, v) {
  var def = servoDefById(id);
  var lo = def ? def.min : 0, hi = def ? def.max : 180;
  return Math.max(lo, Math.min(hi, v));
}

var camLastSent = { pan: 0, tilt: 0 };

function setCamera(newPan, newTilt) {
  // троттлинг: touchmove на телефоне стреляет до 120 раз/с — спам
  // командами серво давал дрожание камеры и нагрев (не чаще 25 Гц)
  var now = Date.now();
  if (newPan !== camPan && now - camLastSent.pan > 40) {
    sendCommand('servo', { id: 1, angle: newPan });
    camPan = newPan;
    camLastSent.pan = now;
  }
  if (newTilt !== camTilt && now - camLastSent.tilt > 40) {
    sendCommand('servo', { id: 2, angle: newTilt });
    camTilt = newTilt;
    camLastSent.tilt = now;
  }
}

function onCamMove(dx, dy, r) {
  // v1.6.1: драг в ту же сторону, что стрелки (см. PanelCore.stickToCam):
  // вверх — камера поднимается, вправо — панорама вправо (pan уменьшается).
  var tgt = PanelCore.stickToCam(dx, dy, r);
  var newPan = camClamp(1, tgt.pan);
  var newTilt = camClamp(2, tgt.tilt);
  setCamera(newPan, newTilt);
  var txt = (Math.abs(dx) < 5 && Math.abs(dy) < 5)
    ? t('joy_cam_default')
    : t('joy_cam_pos_fmt', {p: newPan, t: newTilt});
  setCamLabels(txt);
}
function onCamEnd() {
  setCamLabels(t('joy_cam_default'));
}

var camStick = makeStick(camJoystickContainer, camJoystickKnob, 21, onCamMove, onCamEnd);

// ---- полноэкранный режим камеры (v1.6.8) --------------------------------
// Видео разворачивается на весь экран, слева джойстик движения,
// справа джойстик камеры, снизу мини статус-бар быстрых кнопок
// (фары / габариты / гудок / клешня). Видео-элемент переносится в оверлей,
// поэтому MJPEG-поток не открывается второй раз.
var driveOverlay = document.getElementById('drive-overlay');
var driveStage = document.getElementById('drive-stage');
var cameraWrap = document.querySelector('#card-camera .camera-wrap');
var CAMERA_BODY = 'camera-body';

function enterDriveMode() {
  if (!cameraWrap || !driveStage) return;
  driveStage.insertBefore(cameraWrap, driveStage.firstChild);
  document.body.classList.add('drive-mode');
  // пытаемся занять настоящий fullscreen (спрятать адресную строку браузера);
  // на iOS Safari Fullscreen API нет — оверлей и так закрывает весь экран
  var el = document.documentElement;
  try {
    var p = null;
    if (el.requestFullscreen) p = el.requestFullscreen();
    else if (el.webkitRequestFullscreen) p = el.webkitRequestFullscreen();
    if (p && p.catch) p.catch(function() {});
  } catch (e) {}
  updateVideoFeed();
}
function exitDriveMode() {
  var body = document.getElementById(CAMERA_BODY);
  if (cameraWrap && body) body.insertBefore(cameraWrap, body.firstChild);
  document.body.classList.remove('drive-mode');
  try {
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
    else if (document.webkitFullscreenElement && document.webkitExitFullscreen) document.webkitExitFullscreen();
  } catch (e) {}
  onWheelsEnd();
  onCamEnd();
  updateVideoFeed();
}

var fsEnterBtn = document.getElementById('fs-enter-btn');
if (fsEnterBtn) {
  fsEnterBtn.addEventListener('click', function(e) {
    // кнопка живёт внутри collapsible-заголовка карточки — без
    // stopPropagation клик ещё и сворачивал бы карточку камеры
    e.stopPropagation();
    enterDriveMode();
  });
}
var driveExitBtn = document.getElementById('drive-exit');
if (driveExitBtn) driveExitBtn.addEventListener('click', exitDriveMode);

// Джойстики полноэкранного режима — те же обработчики, другие контейнеры
var fsWheelsStick = makeStick(
  document.getElementById('fs-joystick-container'),
  document.getElementById('fs-joystick-knob'), 24, onWheelsMove, onWheelsEnd);
var fsCamStick = makeStick(
  document.getElementById('fs-cam-joystick-container'),
  document.getElementById('fs-cam-joystick-knob'), 21, onCamMove, onCamEnd);

// ---- клавиатура: W/A/S/D — колёса (A/D сами по себе только руль),
// Q/E — разворот на месте, Пробел — стоп, Esc — аварийный стоп,
// стрелки и I/J/K/L — камера (v1.6.0: стрелки отданы камере) ----
// Камера: стрелки и I/J/K/L, шаг 5° за нажатие. Таблица дельт — в
// panel-core.js (PanelCore.camKeyDelta), проверяется node-тестами:
// вверх = tilt +5, вниз = tilt -5, вправо = pan -5, влево = pan +5
// (v1.6.1: убрана инверсия tilt у стрелок и I/K).
var heldKeys = {};
function typingTarget(e) {
  var tag = e.target && e.target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}
function applyKeyDir() {
  var dir = PanelCore.combineKeys(heldKeys);
  if (!(wheelsStick && wheelsStick.active()) && !(fsWheelsStick && fsWheelsStick.active())) {
    setWheelLabels(dir === 'stop' ? t('joy_wheels_default') : (dirLabel(dir) || dir));
  }
  sendMoveDir(dir, false);
}
document.addEventListener('keydown', function(e) {
  if (typingTarget(e) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape') {
    // Esc: выход из полноэкранного режима + аварийный стоп (всегда)
    if (document.body.classList.contains('drive-mode')) exitDriveMode();
    emergencyStop();
    e.preventDefault();
    return;
  }
  if (e.key === ' ') {
    heldKeys = {};
    sendMoveDir('stop', false);
    e.preventDefault();
    return;
  }
  var ck = PanelCore.camKeyDelta(e.key.length === 1 ? e.key.toLowerCase() : e.key);
  if (ck) {
    setCamera(camClamp(1, camPan + ck[0]), camClamp(2, camTilt + ck[1]));
    e.preventDefault();
    return;
  }
  var act = PanelCore.keyAction(e.key);
  if (!act) return;
  e.preventDefault();               // стрелки не должны скроллить страницу
  if (e.repeat) return;
  heldKeys[act] = true;
  applyKeyDir();
});
document.addEventListener('keyup', function(e) {
  if (typingTarget(e)) return;
  var act = PanelCore.keyAction(e.key);
  if (!act) return;
  delete heldKeys[act];
  applyKeyDir();                    // отпустили одну из двух — едем по оставшейся
});

// Безопасность: потеряли фокус / вкладку скрыли / пальцы ушли — стоп.
// Иначе робот ехал бы дальше с «залипшей» клавишей или зажатым джойстиком.
function releaseAllInputs() {
  heldKeys = {};
  if (wheelsStick) wheelsStick.release();
  if (camStick) camStick.release();
  if (fsWheelsStick) fsWheelsStick.release();
  if (fsCamStick) fsCamStick.release();
  if (lastSentDir !== 'stop') sendMoveDir('stop', false);
}
window.addEventListener('blur', releaseAllInputs);
document.addEventListener('visibilitychange', function() {
  if (document.hidden) releaseAllInputs();
  reportView();
  flushPendingStatus();
  if (!document.hidden) reconnectNow();
});

function emergencyStop() {
  heldKeys = {};
  releaseAllInputs();
  lastSentDir = 'stop';
  sendCommand('estop', {});
  toast(t('toast_estop'), 'error');
}

// ---- Компьютерное зрение: режимы с оверлеем поверх камеры ---------------
// (Color, CV Line, Hand, WatchDog, Yolo*). trackLineCV/trackHand/findColor/
// watchDog стартуют как авто-режимы с камерой — сервер сам вешает оверлей.
document.querySelectorAll('.cv-btn[data-cv]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.cv-btn[data-cv]').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var mode = btn.dataset.cv;
    var handRow = document.getElementById('hand-color-row');
    if (mode === 'none') {
      setCvBadge('none');
      sendCommand('auto', { func: 'stop' });
      sendCommand('cv_mode', { mode: 'none' });
      if (handRow) handRow.style.display = 'none';
    } else if (mode === 'findlineCV') {
      setCvBadge(mode);
      sendCommand('auto', { func: 'trackLineCV' });
      if (handRow) handRow.style.display = 'none';
    } else if (mode === 'trackHand') {
      setCvBadge(mode);
      sendCommand('auto', { func: 'trackHand' });
      if (handRow) handRow.style.display = '';
    } else if (mode === 'findColor') {
      setCvBadge(mode);
      sendCommand('auto', { func: 'findColor' });
      if (handRow) handRow.style.display = '';
    } else if (mode === 'watchDog') {
      setCvBadge(mode);
      sendCommand('auto', { func: 'watchDog' });
      if (handRow) handRow.style.display = 'none';
    } else {
      // чистые CV-режимы (YOLO*): только оверлей, робот стоит
      setCvBadge(mode);
      sendCommand('auto', { func: 'stop' });
      sendCommand('cv_mode', { mode: mode });
      if (handRow) handRow.style.display = 'none';
    }
  });
});

document.querySelectorAll('#hand-color-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#hand-color-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    sendCommand('hand_color', { preset: btn.dataset.preset });
  });
});

function bindHandSlider(id, valId) {
  var el = document.getElementById(id);
  var valEl = document.getElementById(valId);
  if (!el || !valEl) return;
  el.addEventListener('input', function() { valEl.textContent = el.value; });
}
bindHandSlider('hand-h-low', 'hand-h-low-val');
bindHandSlider('hand-s-low', 'hand-s-low-val');
bindHandSlider('hand-v-low', 'hand-v-low-val');
bindHandSlider('hand-h-high', 'hand-h-high-val');
bindHandSlider('hand-s-high', 'hand-s-high-val');
bindHandSlider('hand-v-high', 'hand-v-high-val');

var handColorApply = document.getElementById('hand-color-apply');
if (handColorApply) {
  handColorApply.addEventListener('click', function() {
    sendCommand('hand_color', {
      h_low: parseInt(document.getElementById('hand-h-low').value),
      s_low: parseInt(document.getElementById('hand-s-low').value),
      v_low: parseInt(document.getElementById('hand-v-low').value),
      h_high: parseInt(document.getElementById('hand-h-high').value),
      s_high: parseInt(document.getElementById('hand-s-high').value),
      v_high: parseInt(document.getElementById('hand-v-high').value),
    });
    document.querySelectorAll('#hand-color-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    toast(t('toast_hand_color'), 'success');
  });
}

var speedSlider = document.getElementById('speed-slider');
var speedVal = document.getElementById('speed-val');
if (speedSlider) {
  speedSlider.addEventListener('input', function() { speedVal.textContent = speedSlider.value + '%'; });
  speedSlider.addEventListener('change', function() { sendCommand('speed', { value: parseInt(speedSlider.value) }); });
}

document.querySelectorAll('.crane-arm-btn').forEach(function(btn) {
  btn.addEventListener('click', toggleCraneArm);
});

document.querySelectorAll('.crane-grip-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var action = btn.dataset.action;
    if (action === 'grip_low') craneGripPosition = 'low';
    else if (action === 'grip_mid') craneGripPosition = 'mid';
    // v1.6.4: grip_high удалён — осталось low/mid
    sendCommand('crane', { action: action });
    updateCraneGripUI();
  });
});

document.getElementById('hl-main').addEventListener('click', toggleHeadlight);
// v1.6.4: боковые габариты (Side lights) — кнопка в окне фар + геймпад L1
document.getElementById('hl-side').addEventListener('click', toggleSideLights);

document.getElementById('hl-left-signal').addEventListener('click', function() {
  hlLeftSignal = !hlLeftSignal;
  if (hlLeftSignal) {
    hlRightSignal = false;
    document.getElementById('hl-right-signal').className = 'signal-btn off';
    var rDot = document.getElementById('blinker-right-dot');
    if (rDot) rDot.dataset.on = '0';
    refreshBlinkerText('right');
  }
  sendCommand('blinker', { side: 'left', active: hlLeftSignal });
  this.className = 'signal-btn ' + (hlLeftSignal ? 'on' : 'off');
  var lDot = document.getElementById('blinker-left-dot');
  if (lDot) lDot.dataset.on = hlLeftSignal ? '1' : '0';
  refreshBlinkerText('left');
});

document.getElementById('hl-right-signal').addEventListener('click', function() {
  hlRightSignal = !hlRightSignal;
  if (hlRightSignal) {
    hlLeftSignal = false;
    document.getElementById('hl-left-signal').className = 'signal-btn off';
    var lDot = document.getElementById('blinker-left-dot');
    if (lDot) lDot.dataset.on = '0';
    refreshBlinkerText('left');
  }
  sendCommand('blinker', { side: 'right', active: hlRightSignal });
  this.className = 'signal-btn ' + (hlRightSignal ? 'on' : 'off');
  var rDot = document.getElementById('blinker-right-dot');
  if (rDot) rDot.dataset.on = hlRightSignal ? '1' : '0';
  refreshBlinkerText('right');
});

var ledColorInput = document.getElementById('led-color');

document.querySelectorAll('.color-preset').forEach(function(btn) {
  btn.addEventListener('click', function() {
    ledColorInput.value = btn.dataset.hex;
    if (currentLedMode !== 'off' && currentLedMode !== 'rainbow' && currentLedMode !== 'police') {
      sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
    }
  });
});
ledColorInput.addEventListener('input', function() {
  if (currentLedMode === 'solid' || currentLedMode === 'breath' || currentLedMode === 'flow' || currentLedMode === 'colorWipe') {
    sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
  }
});
ledColorInput.addEventListener('change', function() {
  if (currentLedMode !== 'off' && currentLedMode !== 'rainbow' && currentLedMode !== 'police') {
    sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
  }
});
document.querySelectorAll('#led-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#led-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    currentLedMode = btn.dataset.led;
    sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
  });
});

document.querySelectorAll('#buzzer-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    if (btn.dataset.buzzer === 'stop') {
      sendCommand('buzzer_stop', {});
    } else {
      sendCommand('buzzer', { melody: btn.dataset.buzzer });
    }
  });
});

updateCraneArmUI();
updateCraneGripUI();

// ---- Автономные режимы (v1.6.8): Radar, Auto Drive, IR Line, Distance,
// Grab, Maze U. Режимы с оверлеем камеры вынесены в «Компьютерное зрение».
// Кнопка «Stop» заменена на «АВАРИЙНЫЙ СТОП» (estop) — задание v1.6.8.
document.querySelectorAll('#auto-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#auto-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    sendCommand('auto', { func: btn.dataset.auto });
  });
});
var autoEstopBtn = document.getElementById('auto-estop');
if (autoEstopBtn) autoEstopBtn.addEventListener('click', emergencyStop);

var voiceStartBtn = document.getElementById('voice-start-btn');
var voiceStopBtn = document.getElementById('voice-stop-btn');
if (voiceStartBtn) {
  voiceStartBtn.addEventListener('click', function() {
    sendCommand('voice', { action: 'start' });
  });
}
if (voiceStopBtn) {
  voiceStopBtn.addEventListener('click', function() {
    sendCommand('voice', { action: 'stop' });
  });
}

var steadyToggle = document.getElementById('steady-toggle');
if (steadyToggle) {
  steadyToggle.addEventListener('click', function() {
    sendCommand('steady', { action: 'toggle' });
  });
}

// --- Push Mode: расталкивание препятствий клешнёй по HC-SR04 ----------------
var portedModes = {
  push:  { btn: 'push-toggle', cmd: 'push', active: false },
};

Object.keys(portedModes).forEach(function(key) {
  var m = portedModes[key];
  var btn = document.getElementById(m.btn);
  if (!btn) return;
  btn.addEventListener('click', function() {
    var action = m.active ? 'stop' : 'start';
    sendCommand(m.cmd, { action: action });
    // оптимистичное переключение — статус с сервера подтвердит/откатит
    setPortedMode(key, !m.active);
    toast(m.active ? t('toast_push_stopped') : t('toast_push_started'));
  });
});

function setPortedMode(key, active) {
  var m = portedModes[key];
  if (!m) return;
  m.active = !!active;
  var btn = document.getElementById(m.btn);
  if (btn) {
    btn.classList.toggle('active', m.active);
    btn.textContent = m.active ? t('push_btn_on') : t('push_btn_off');
  }
}

var btScanBtn = document.getElementById('bt-scan-btn');
var btAutoBtn = document.getElementById('bt-auto-btn');
var btDisconnectBtn = document.getElementById('bt-disconnect-btn');
var btDeviceList = document.getElementById('bt-device-list');
var btDevices = document.getElementById('bt-devices');
var btScanning = document.getElementById('bt-scanning');
var btSavedInfo = document.getElementById('bt-saved-info');

function btMsg(text, color) {
  // имя устройства приходит из эфира (его задаёт кто угодно рядом) — только textContent
  btDevices.textContent = '';
  var d = document.createElement('div');
  d.style.cssText = 'font-size:.75rem;padding:4px;color:' + (color || '#5f6368');
  d.textContent = text;
  btDevices.appendChild(d);
  btDeviceList.style.display = '';
}

function renderBtDevices(devs) {
  btDevices.textContent = '';
  devs.forEach(function(dev) {
    var item = document.createElement('div');
    item.className = 'bt-device-item' + (dev.is_gamepad ? ' bt-gamepad' : '');
    var label = document.createElement('span');
    label.className = 'bt-device-name';
    var tags = [];
    if (dev.connected) tags.push(t('bt_tag_connected')); else if (dev.paired) tags.push(t('bt_tag_paired'));
    if (dev.rssi !== null && dev.rssi !== undefined) tags.push(dev.rssi + ' dBm');
    label.textContent = dev.name + ' (' + dev.mac + ')' + (tags.length ? ' \u00B7 ' + tags.join(', ') : '');
    var btn = document.createElement('button');
    btn.className = 'btn-sm btn-primary bt-connect-btn';
    btn.dataset.mac = dev.mac;
    btn.textContent = dev.connected ? t('bt_state_connected')
      : (dev.paired ? t('bt_state_connect') : t('bt_state_pair'));
    btn.disabled = !!dev.connected;
    btn.addEventListener('click', function() {
      var label0 = btn.textContent;
      btn.textContent = '...';
      btn.disabled = true;
      if (!dev.paired) toast(t('toast_pairing_hint'), 'info');
      fetch('/api/bt/connect', { method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ mac: dev.mac, name: dev.name }) })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d.ok) { toast(t('toast_connected'), 'success'); btn.textContent = t('bt_state_connected'); }
          else { toast(d.message || d.error || t('toast_conn_failed'), 'error'); btn.textContent = label0; btn.disabled = false; }
        })
        .catch(function() { toast(t('toast_conn_error'), 'error'); btn.textContent = label0; btn.disabled = false; });
    });
    item.appendChild(label);
    item.appendChild(btn);
    btDevices.appendChild(item);
  });
  btDeviceList.style.display = '';
}

btScanBtn.addEventListener('click', function() {
  if (btScanBtn.disabled) return;
  btScanBtn.disabled = true;
  btScanning.style.display = '';
  btDeviceList.style.display = 'none';
  btDevices.textContent = '';
  fetch('/api/bt/scan', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      btScanning.style.display = 'none';
      btScanBtn.disabled = false;
      if (d.ok === false) {
        var hint = d.error || t('toast_scan_failed');
        if (d.diagnose && d.diagnose.rfkill_blocked) hint += t('toast_scan_rfkill');
        btMsg(hint, '#ea4335');
      } else if (d.devices && d.devices.length > 0) {
        renderBtDevices(d.devices);
      } else {
        btMsg(t('toast_scan_empty'));
      }
    })
    .catch(function() {
      btScanning.style.display = 'none';
      btScanBtn.disabled = false;
      btMsg(t('toast_scan_failed'), '#ea4335');
    });
});

btAutoBtn.addEventListener('click', function() {
  fetch('/api/bt/auto_connect', { method: 'POST', headers: authHeaders() }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast(t('toast_auto_connect_ok'), 'success');
    else toast(d.error || d.message || t('toast_auto_connect_fail'), 'error');
  }).catch(function() { toast(t('toast_auto_connect_error'), 'error'); });
});

btDisconnectBtn.addEventListener('click', function() {
  fetch('/api/bt/disconnect', { method: 'POST', headers: authHeaders() }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast(t('toast_disconnected'), 'success');
    else toast(d.error || t('toast_disconnect_failed'), 'error');
  }).catch(function() { toast(t('toast_disconnect_error'), 'error'); });
});

fetch('/api/bt/status', { headers: (function(){ var h = {}; var tok = getToken(); if (tok) h['X-Auth-Token'] = tok; return h; })() }).then(function(r) { return r.json(); }).then(function(d) {
  if (d.saved_mac && btSavedInfo) {
    btSavedInfo.textContent = t('bt_saved_fmt', {s: d.saved_mac});
  }
}).catch(function() {});

function getLogLevel(text) {
  if (!text) return 'debug';
  var up = text.toUpperCase();
  if (up.indexOf('| ERROR') !== -1 || up.indexOf('[ERROR]') !== -1) return 'error';
  if (up.indexOf('| WARNING') !== -1 || up.indexOf('| WARN') !== -1 || up.indexOf('[WARN') !== -1) return 'warn';
  if (up.indexOf('| DEBUG') !== -1 || up.indexOf('[DEBUG]') !== -1) return 'debug';
  if (up.indexOf('| INFO') !== -1 || up.indexOf('[INFO]') !== -1) return 'info';
  return 'info';
}

var MAX_CONSOLE_LINES = 300;
var CONSOLE_HISTORY_LINES = 250;

function appendConsoleLine(text, ts) {
  var output = document.getElementById('console-output');
  if (!output) return;
  var level = getLogLevel(text);
  logCounts[level] = (logCounts[level] || 0) + 1;
  // пока вкладка Console не активна — только счётчики: DOM консоли
  // на телефоне не растёт из-за фоновых логов прошивки
  var consoleTab = document.getElementById('content-console');
  if (consoleTab && consoleTab.classList.contains('active')) {
    var line = document.createElement('div');
    line.className = 'log-line log-' + level;
    line.dataset.level = level;
    if (ts) {
      var d = new Date(ts * 1000);
      text = d.toLocaleTimeString() + ' ' + text;
    }
    line.textContent = text;
    line.style.display = logFilters[level] ? '' : 'none';
    output.appendChild(line);
    while (output.children.length > MAX_CONSOLE_LINES) {
      output.removeChild(output.firstChild);
    }
    if (consoleAutoScroll) output.scrollTop = output.scrollHeight;
    var countEl = document.getElementById('console-line-count');
    if (countEl) countEl.textContent = t('console_lines_fmt', {n: output.children.length});
  }
  updateLogCounters();
}

function updateLogCounters() {
  ['info', 'warn', 'error', 'debug'].forEach(function(level) {
    var el = document.getElementById('log-count-' + level);
    if (el) el.textContent = logCounts[level] || 0;
  });
}

function applyLogFilters() {
  var output = document.getElementById('console-output');
  if (!output) return;
  output.querySelectorAll('.log-line').forEach(function(line) {
    var level = line.dataset.level || 'info';
    line.style.display = logFilters[level] ? '' : 'none';
  });
}

function applyLogSort() {
  var output = document.getElementById('console-output');
  if (!output) return;
  var lines = Array.from(output.querySelectorAll('.log-line'));
  var levelOrder = { error: 0, warn: 1, info: 2, debug: 3 };
  if (logSortMode === 'level') {
    lines.sort(function(a, b) {
      var la = levelOrder[a.dataset.level] || 2;
      var lb = levelOrder[b.dataset.level] || 2;
      return la - lb;
    });
    lines.forEach(function(line) { output.appendChild(line); });
  }
}

document.querySelectorAll('.log-filter-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var level = btn.dataset.level;
    logFilters[level] = !logFilters[level];
    btn.classList.toggle('active', logFilters[level]);
    applyLogFilters();
  });
});

document.getElementById('log-sort-select').addEventListener('change', function() {
  logSortMode = this.value;
  applyLogSort();
});

document.getElementById('console-autoscroll').addEventListener('change', function() {
  consoleAutoScroll = this.checked;
});

document.getElementById('console-clear-btn').addEventListener('click', function() {
  var output = document.getElementById('console-output');
  output.innerHTML = '';
  logCounts = { info: 0, warn: 0, error: 0, debug: 0 };
  updateLogCounters();
  var countEl = document.getElementById('console-line-count');
  if (countEl) countEl.textContent = t('console_lines_fmt', {n: 0});
  sendCommand('clear_log', {});
});


// ============================ Миссии ============================
var missionSel = document.getElementById('mission-select');
var missionState = {active: false, recording: false, has_path: false};

function refreshMissions() { sendCommand('mission', {action: 'list'}); }

function renderMissionList(list) {
  if (!missionSel) return;
  var prev = missionSel.value;
  missionSel.innerHTML = '';
  list.forEach(function(m) {
    var o = document.createElement('option');
    o.value = m.name;
    o.dataset.builtin = m.builtin ? '1' : '0';
    o.textContent = t('mission_steps_fmt', {s: m.name, n: m.steps, sec: m.sec}) + (m.builtin ? '' : ' *');
    missionSel.appendChild(o);
  });
  if (prev) missionSel.value = prev;
  updateMissionButtons();
}

function updateMissionButtons() {
  var st = missionState;
  var sel = missionSel && missionSel.options[missionSel.selectedIndex];
  document.getElementById('mission-start').disabled = st.active || st.recording || !sel;
  document.getElementById('mission-stop').disabled = !st.active;
  document.getElementById('mission-home').disabled = st.active || st.recording || !st.has_path;
  document.getElementById('mission-rec').textContent = st.recording ? t('mission_rec_stop') : t('mission_rec');
  document.getElementById('mission-rec').dataset.rec = st.recording ? '1' : '0';
  document.getElementById('mission-del').style.display =
    (sel && sel.dataset.builtin === '0') ? '' : 'none';
}

function renderMissionStatus(s) {
  if (!s) return;
  ['active', 'recording', 'has_path'].forEach(function(k) {
    if (s[k] !== undefined) missionState[k] = !!s[k];
  });
  var el = document.getElementById('mission-status');
  if (el) {
    var txt = t('mission_idle'), st = s.state || 'idle';
    if (missionState.recording) txt = t('mission_txt_recording_fmt', {n: s.rec_events || 0});
    else if (missionState.active) txt = t('mission_txt_running_fmt', {s: s.name || '', done: s.done || 0, n: s.total || 0});
    else if (st === 'done') txt = t('mission_txt_done_fmt', {s: s.name || ''});
    else if (st === 'stopped') txt = t('mission_txt_stopped');
    else if (st === 'obstacle') txt = t('mission_txt_obstacle');
    else if (st === 'error') txt = t('mission_txt_error_fmt', {s: s.error || '?'});
    el.textContent = txt;
    el.dataset.state = missionState.active ? 'running'
      : (missionState.recording ? 'recording' : st);
  }
  updateMissionButtons();
}

document.getElementById('mission-start').addEventListener('click', function() {
  if (missionSel && missionSel.value) sendCommand('mission', {action: 'start', name: missionSel.value});
});
document.getElementById('mission-stop').addEventListener('click', function() {
  sendCommand('mission', {action: 'stop'});
});
document.getElementById('mission-home').addEventListener('click', function() {
  sendCommand('mission', {action: 'return_home'});
});
document.getElementById('mission-rec').addEventListener('click', function() {
  if (!missionState.recording) {
    sendCommand('mission', {action: 'record_start'});
  } else {
    var name = '';
    try { name = (window.prompt(t('mission_prompt_save'), '') || '').trim(); } catch (e) {}
    sendCommand('mission', {action: 'record_stop', save_as: name});
  }
});
document.getElementById('mission-del').addEventListener('click', function() {
  if (missionSel && missionSel.value &&
      (!window.confirm || window.confirm(t('mission_confirm_delete', {s: missionSel.value})))) {
    sendCommand('mission', {action: 'delete', name: missionSel.value});
  }
});
if (missionSel) missionSel.addEventListener('change', updateMissionButtons);

// ============================ Обновление ============================
function updMsg(text, state) {
  var el = document.getElementById('upd-status');
  el.textContent = text;
  el.dataset.state = state || '';
}

function loadUpdateInfo() {
  return fetch('/api/update/info', {headers: authHeaders()})
    .then(function(r) { return r.json(); })
    .then(function(i) {
      if (!i || !i.ok) return i;
      document.getElementById('upd-version').textContent = 'v' + i.version;
      var sel = document.getElementById('upd-backups');
      sel.innerHTML = '';
      i.backups.forEach(function(b) {
        var o = document.createElement('option');
        o.value = b.name;
        o.textContent = b.name.replace(/^backup_|\.tar\.gz$/g, '') + ' (' + Math.round(b.size / 1024) + ' KB)';
        sel.appendChild(o);
      });
      document.getElementById('upd-rollback').disabled = !i.backups.length;
      if (i.pending) updMsg(t('upd_pending'), 'running');
      return i;
    })
    .catch(function() {});
}

// После установки сервис перезапускается: ждём возвращения и перезагружаем страницу
function waitForRestart(expectVersion) {
  updMsg(t('upd_installed'), 'running');
  var tries = 0;
  var timer = setInterval(function() {
    if (++tries > 60) { clearInterval(timer); updMsg(t('upd_timeout'), 'error'); return; }
    fetch('/api/update/info', {headers: authHeaders(), cache: 'no-store'})
      .then(function(r) { return r.json(); })
      .then(function(i) {
        if (i && i.ok && (!expectVersion || i.version === expectVersion)) { clearInterval(timer); location.reload(); }
      })
      .catch(function() {});
  }, 2000);
}

function updateResult(r) {
  if (!r || !r.ok) { updMsg(t('upd_failed', {s: (r && r.error) || '?'}), 'error'); return; }
  var note = (r.config_changed && r.config_changed.length)
    ? t('upd_config_note', {s: r.config_changed.slice(0, 6).join(', ')}) : '';
  if (r.restarting) { updMsg(t('upd_updated_fmt', {a: r.from, b: r.to}) + note, 'running'); waitForRestart(r.to); }
  else updMsg(t('upd_updated_fmt', {a: r.from, b: r.to}) + t('upd_manual_restart') + note, 'done');
  loadUpdateInfo();
}

// Кастомный выбор файла: показываем имя выбранного архива в псевдо-кнопке
var updFileInput = document.getElementById('upd-file');
if (updFileInput) {
  updFileInput.addEventListener('change', function() {
    var nameEl = document.getElementById('upd-file-name');
    if (nameEl) nameEl.textContent = this.files.length ? this.files[0].name : t('upd_choose');
  });
}

document.getElementById('upd-go').addEventListener('click', function() {
  var f = document.getElementById('upd-file').files[0];
  if (!f) { updMsg(t('upd_choose_first'), 'error'); return; }
  if (window.confirm && !window.confirm(t('upd_install_confirm', {s: f.name}))) return;
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/update');
  var h = authHeaders();
  Object.keys(h).forEach(function(k) { if (k.toLowerCase() !== 'content-type') xhr.setRequestHeader(k, h[k]); });
  xhr.upload.onprogress = function(e) {
    if (e.lengthComputable) updMsg(t('upd_uploading', {n: Math.round(e.loaded * 100 / e.total)}), 'running');
  };
  xhr.onload = function() {
    var r = null;
    try { r = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status === 401) { handleUnauthorized(); return; }
    if (xhr.status === 409) { updMsg(t('upd_not_driver'), 'error'); return; }
    updateResult(r);
  };
  xhr.onerror = function() { updMsg(t('upd_upload_lost'), 'error'); };
  var fd = new FormData();
  fd.append('file', f);
  updMsg(t('upd_uploading', {n: 0}), 'running');
  xhr.send(fd);
});

document.getElementById('upd-rollback').addEventListener('click', function() {
  var name = document.getElementById('upd-backups').value;
  if (!name || (window.confirm && !window.confirm(t('upd_rollback_confirm', {s: name})))) return;
  fetch('/api/update/rollback', {method: 'POST', headers: authHeaders(), body: JSON.stringify({name: name})})
    .then(function(r) { return r.json(); })
    .then(updateResult)
    .catch(function() { updMsg(t('upd_rollback_failed'), 'error'); });
});

loadUpdateInfo();

// ---- переключатель языка (RU по умолчанию / EN) ----
var langRuBtn = document.getElementById('lang-ru');
var langEnBtn = document.getElementById('lang-en');
if (langRuBtn) langRuBtn.addEventListener('click', function() { setLang('ru'); });
if (langEnBtn) langEnBtn.addEventListener('click', function() { setLang('en'); });

// ---- быстрые кнопки мини статус-бара полноэкранного режима ----
var fsHlMain = document.getElementById('fs-hl-main');
if (fsHlMain) fsHlMain.addEventListener('click', toggleHeadlight);
var fsHlSide = document.getElementById('fs-hl-side');
if (fsHlSide) fsHlSide.addEventListener('click', toggleSideLights);
var fsBuzzer = document.getElementById('fs-buzzer');
if (fsBuzzer) {
  fsBuzzer.addEventListener('click', function() {
    sendCommand('buzzer', { melody: 'beep' });
    // короткая вспышка — гудок мгновенный, состояния не хранит
    fsBuzzer.classList.add('on');
    setTimeout(function() { fsBuzzer.classList.remove('on'); }, 300);
  });
}
var fsCraneArm = document.getElementById('fs-crane-arm');
if (fsCraneArm) fsCraneArm.addEventListener('click', toggleCraneArm);

// кнопки шапки
document.getElementById('estop-btn').addEventListener('click', emergencyStop);
[document.getElementById('take-control-btn'), document.getElementById('take-control-banner-btn')]
  .forEach(function(b) {
    if (b) b.addEventListener('click', function() { sendCommand('take_control', {}); setDriver(CLIENT_ID); });
  });
document.querySelectorAll('.tab-btn').forEach(function(b) {
  b.addEventListener('click', function() { setTimeout(reportView, 0); });
});
// высота липкой статус-панели нужна CSS (липкое видео на телефоне)
(function() {
  var sb = document.getElementById('status-bar');
  function setH() { document.documentElement.style.setProperty('--sb-h', sb.offsetHeight + 'px'); }
  setH();
  if (window.ResizeObserver) new ResizeObserver(setH).observe(sb);
  else window.addEventListener('resize', setH);
})();

// ---- инициализация ----
setLang(LANG);          // применяет словарь и подсвечивает активный язык
restoreCollapsed();
wsConnect();
fetchConsoleHistory();
updateVideoFeed();
