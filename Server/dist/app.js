var servoDefs = [
  { id: 0, name: 'Steering', min: 30, max: 150, init: 90 },
  { id: 1, name: 'Cam Pan',  min: 0,  max: 180, init: 90 },
  { id: 2, name: 'Cam Tilt', min: 0,  max: 180, init: 90 },
  { id: 3, name: 'Servo 3',  min: 0,  max: 180, init: 90 },
  { id: 4, name: 'Servo 4',  min: 0,  max: 180, init: 90 },
  { id: 5, name: 'Crane Grip', min: 0, max: 190, init: 190 },
  { id: 6, name: 'Crane Arm', min: 0,  max: 180, init: 80 },
];

var hlMainOn = false;
var hlLeftSignal = false;
var hlRightSignal = false;
var currentLedMode = 'off';
var lastSentDir = 'stop';
var moveThrottle = 0;

var craneArmClosed = false;
var craneGripPosition = 'high';

var logCounts = { info: 0, warn: 0, error: 0, debug: 0 };
var logFilters = { info: true, warn: true, error: true, debug: true };
var logSortMode = 'time';
var logLines = [];

var hw = {
  motors: false, servos: false, leds: false, buzzer: false,
  switches: false, ultrasonic: false, mpu6050: false,
  oled: false, camera: false, autonomous: false, crane: false,
  ds4: false, voice: false,
};

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

document.querySelectorAll('.collapsible-header').forEach(function(header) {
  header.addEventListener('click', function() {
    var targetId = header.dataset.target;
    if (!targetId) return;
    var body = document.getElementById(targetId);
    if (!body) return;
    header.classList.toggle('open');
    body.classList.toggle('open');
  });
});

function updateHardwareUI(hardwareStatus) {
  if (!hardwareStatus) return;
  hw = hardwareStatus;
  toggleHwSection('card-autonomous', 'auto-missing-tag', hw.autonomous);
  toggleHwSection(null, 'servo-missing-tag', hw.servos);
  toggleHwSection('card-headlights', 'hl-missing-tag', hw.switches);
  toggleHwSection('card-led', 'led-missing-tag', hw.leds);
  toggleHwSection('card-buzzer', 'buzzer-missing-tag', hw.buzzer);
  toggleHwSection(null, 'mpu-missing-tag', hw.mpu6050);
  toggleHwSection(null, 'claw-missing-tag', hw.crane);
  toggleHwSection('card-voice', 'voice-missing-tag', hw.voice);
}

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

var ws = null;
var wsReconnectTimer = null;
var usePolling = false;
var wsHost = location.hostname;
var wsPort = 8888;

function wsConnect() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  var url = 'ws://' + wsHost + ':' + wsPort;
  try {
    ws = new WebSocket(url);
    ws.onopen = function() {
      document.getElementById('connection-dot').classList.remove('offline');
      usePolling = false;
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        var msgType = data.type || '';
        var msgData = data.data || {};
        if (msgType === 'status') updateStatus(msgData);
        else if (msgType === 'log') appendConsoleLine(data.text);
        else if (msgType === 'log_history') {
          if (data.lines && data.lines.length) {
            data.lines.forEach(function(item) { appendConsoleLine(item[1], item[0]); });
          }
        }
        else if (msgType === 'response') {
          if (msgData.error) toast(msgData.error, 'error');
          if (msgData.cmd === 'i2c_scan' && msgData.ok) {
            var el = document.getElementById('i2c-scan-result');
            if (el) showI2CResult(msgData, el);
          }
          if (msgData.cmd === 'servo_get_limits' && msgData.ok) {
            applyServoLimits(msgData.limits);
          }
        }
      } catch(err) {}
    };
    ws.onclose = function() {
      document.getElementById('connection-dot').classList.add('offline');
      wsReconnectTimer = setTimeout(wsConnect, 3000);
    };
    ws.onerror = function() { ws.close(); };
  } catch(e) { usePolling = true; startPolling(); }
}

function sendCommand(cmd, params) {
  params = params || {};
  if (cmd === 'move' || cmd === 'speed') {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({cmd: 'web_active', params: {active: true}}));
    }
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd: cmd, params: params}));
  } else {
    var urlMap = {
      'move': '/cmd/move', 'speed': '/cmd/speed', 'servo': '/cmd/servo',
      'servo_home': '/cmd/servo_home', 'led': '/cmd/led', 'buzzer': '/cmd/buzzer',
      'buzzer_stop': '/cmd/buzzer_stop', 'switch': '/cmd/switch',
      'cv_mode': '/cmd/cv_mode', 'auto': '/cmd/auto', 'crane': '/cmd/crane',
      'voice': '/cmd/voice', 'headlight': '/cmd/headlight', 'blinker': '/cmd/blinker',
    };
    var url = urlMap[cmd];
    if (url) {
      fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(params) }).catch(function() {});
    }
  }
}

var pollTimer = null;
function startPolling() {
  if (pollTimer) return;
  function poll() {
    fetch('/api/status').then(function(r) { return r.json(); }).then(function(d) {
      updateStatus(d); document.getElementById('connection-dot').classList.remove('offline');
    }).catch(function() { document.getElementById('connection-dot').classList.add('offline'); });
    pollTimer = setTimeout(poll, 1500);
  }
  poll();
}
function startSSE() {
  try {
    var source = new EventSource('/api/status/stream');
    source.onmessage = function(e) {
      try { var d = JSON.parse(e.data); updateStatus(d); document.getElementById('connection-dot').classList.remove('offline'); } catch(err) {}
    };
    source.onerror = function() { document.getElementById('connection-dot').classList.add('offline'); source.close(); startPolling(); };
  } catch(e) { startPolling(); }
}

var firstStatus = true;
function updateStatus(d) {
  if (!d) return;
  if (d.cpu_temp !== undefined) document.getElementById('sb-cpu-temp').textContent = d.cpu_temp + '\u00B0C';
  if (d.cpu_usage !== undefined) document.getElementById('sb-cpu-usage').textContent = d.cpu_usage + '%';
  if (d.ram_percent !== undefined) {
    var ramText;
    if (d.ram_used_mb !== undefined && d.ram_total_mb !== undefined) {
      ramText = d.ram_used_mb + '/' + d.ram_total_mb + 'M ' + d.ram_percent + '%';
    } else {
      ramText = d.ram_used + '/' + d.ram_total + 'G ' + d.ram_percent + '%';
    }
    document.getElementById('sb-ram').textContent = ramText;
  }
  if (d.distance !== undefined) document.getElementById('sb-distance').textContent = d.distance + 'cm';
  if (d.speed !== undefined) document.getElementById('sb-speed').textContent = d.speed + '%';
  var autoModeLabels = {
    'none': 'Ready', 'radarScan': 'Radar', 'automatic': 'Auto Drive',
    'trackLine': 'IR Line', 'trackLineCV': 'CV Line', 'trackHand': 'Hand Track',
    'keepDistance': 'Distance'
  };
  document.getElementById('sb-module').textContent = autoModeLabels[d.auto_mode || 'none'] || d.auto_mode || 'Ready';
  if (d.hw) {
    updateHardwareUI(d.hw);
    if (firstStatus) { firstStatus = false; }
  }
  if (d.servo_limits) {
    applyServoLimits(d.servo_limits);
  }
  var mpu = d.mpu6050;
  if (mpu) {
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
  if (d.ir_left !== undefined && d.ir_left !== null) {
    document.getElementById('ir-left-val').textContent = d.ir_left ? 'LINE' : 'CLEAR';
    document.getElementById('ir-left-val').style.color = d.ir_left ? '#ea4335' : '#34a853';
  } else {
    document.getElementById('ir-left-val').textContent = 'N/A';
    document.getElementById('ir-left-val').style.color = '#9aa0a6';
  }
  if (d.ir_middle !== undefined && d.ir_middle !== null) {
    document.getElementById('ir-middle-val').textContent = d.ir_middle ? 'LINE' : 'CLEAR';
    document.getElementById('ir-middle-val').style.color = d.ir_middle ? '#ea4335' : '#34a853';
  } else {
    document.getElementById('ir-middle-val').textContent = 'N/A';
    document.getElementById('ir-middle-val').style.color = '#9aa0a6';
  }
  if (d.ir_right !== undefined && d.ir_right !== null) {
    document.getElementById('ir-right-val').textContent = d.ir_right ? 'LINE' : 'CLEAR';
    document.getElementById('ir-right-val').style.color = d.ir_right ? '#ea4335' : '#34a853';
  } else {
    document.getElementById('ir-right-val').textContent = 'N/A';
    document.getElementById('ir-right-val').style.color = '#9aa0a6';
  }
  var lvBanner = document.getElementById('low-voltage-banner');
  if (lvBanner) {
    lvBanner.style.display = d.low_voltage ? 'block' : 'none';
  }
  if (d.headlight !== undefined) {
    hlMainOn = d.headlight;
    document.getElementById('hl-main').className = 'headlight-btn ' + (hlMainOn ? 'on' : 'off');
  }
  if (d.left_blinker !== undefined) {
    hlLeftSignal = d.left_blinker;
    document.getElementById('hl-left-signal').className = 'headlight-btn ' + (hlLeftSignal ? 'on' : 'off');
    var leftStatusEl = document.getElementById('blinker-left-status');
    if (leftStatusEl) { leftStatusEl.textContent = hlLeftSignal ? 'ON' : 'OFF'; leftStatusEl.style.color = hlLeftSignal ? '#fdd663' : '#9aa0a6'; }
  }
  if (d.right_blinker !== undefined) {
    hlRightSignal = d.right_blinker;
    document.getElementById('hl-right-signal').className = 'headlight-btn ' + (hlRightSignal ? 'on' : 'off');
    var rightStatusEl = document.getElementById('blinker-right-status');
    if (rightStatusEl) { rightStatusEl.textContent = hlRightSignal ? 'ON' : 'OFF'; rightStatusEl.style.color = hlRightSignal ? '#fdd663' : '#9aa0a6'; }
  }
  var ds4 = d.ds4;
  if (ds4) {
    var ds4Dot = document.getElementById('ds4-status-dot');
    var ds4Text = document.getElementById('ds4-status-text');
    if (ds4.connected) {
      document.getElementById('sb-ds4').textContent = ds4.speed + '%';
      document.getElementById('sb-ds4').style.color = '#34a853';
      if (ds4Dot) ds4Dot.style.background = '#34a853';
      if (ds4Text) { ds4Text.textContent = 'Connected (speed ' + ds4.speed + '%)'; ds4Text.style.color = '#34a853'; }
      document.getElementById('bt-disconnect-btn').style.display = '';
    } else {
      document.getElementById('sb-ds4').textContent = 'Searching';
      document.getElementById('sb-ds4').style.color = '#fdd663';
      if (ds4Dot) ds4Dot.style.background = '#fdd663';
      if (ds4Text) { ds4Text.textContent = 'Searching...'; ds4Text.style.color = '#fdd663'; }
      document.getElementById('bt-disconnect-btn').style.display = 'none';
    }
    if (ds4.crane_arm_closed !== undefined) {
      craneArmClosed = ds4.crane_arm_closed;
      updateCraneArmUI();
    }
    if (ds4.crane_grip !== undefined) {
      craneGripPosition = ds4.crane_grip;
      updateCraneGripUI();
    }
  } else {
    document.getElementById('sb-ds4').textContent = 'OFF';
    document.getElementById('sb-ds4').style.color = '#9aa0a6';
  }
  if (d.crane_arm_closed !== undefined) {
    craneArmClosed = d.crane_arm_closed;
    updateCraneArmUI();
  }
  if (d.crane_grip_position !== undefined) {
    craneGripPosition = d.crane_grip_position;
    updateCraneGripUI();
  }

  var voice = d.voice;
  if (voice) {
    if (voice.available) {
      document.getElementById('voice-missing-tag').style.display = 'none';
      var vc = document.getElementById('card-voice');
      if (vc) vc.classList.remove('hw-missing');
    } else {
      document.getElementById('voice-missing-tag').style.display = '';
    }
    voiceActive = voice.active;
    voiceAvailable = voice.available;
    if (voice.last_command) {
      document.getElementById('voice-last-cmd').textContent = voice.last_command;
    }
    updateVoiceUI();
  } else {
    voiceAvailable = false;
    updateVoiceUI();
  }
}


wsConnect();
setTimeout(startSSE, 500);

var consoleHistoryLoaded = false;

function fetchConsoleHistory() {
  if (consoleHistoryLoaded) return;
  consoleHistoryLoaded = true;
  fetch('/api/logs').then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok && d.lines && d.lines.length) {
      d.lines.forEach(function(item) { appendConsoleLine(item[1], item[0]); });
    }
  }).catch(function() {});
}

document.querySelectorAll('.tab-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    btn.classList.add('active');
    document.getElementById('content-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'info') loadDocs();
    if (btn.dataset.tab === 'console') fetchConsoleHistory();
  });
});

document.querySelectorAll('.cv-btn[data-cv]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.cv-btn[data-cv]').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var mode = btn.dataset.cv;
    var badge = document.getElementById('cv-badge');
    if (mode === 'findlineCV') {
      badge.textContent = 'CV: Line Follow';
      badge.classList.toggle('visible', true);
      sendCommand('auto', { func: 'trackLineCV' });
    } else if (mode === 'trackHand') {
      badge.textContent = 'CV: Hand Track';
      badge.classList.toggle('visible', true);
      sendCommand('auto', { func: 'trackHand' });
    } else {
      badge.textContent = 'CV: ' + mode.charAt(0).toUpperCase() + mode.slice(1);
      badge.classList.toggle('visible', mode !== 'none');
      if (mode !== 'none') sendCommand('auto', { func: 'stop' });
      sendCommand('cv_mode', { mode: mode });
    }
  });
});

var speedSlider = document.getElementById('speed-slider');
var speedVal = document.getElementById('speed-val');
speedSlider.addEventListener('input', function() { speedVal.textContent = speedSlider.value + '%'; });
speedSlider.addEventListener('change', function() { sendCommand('speed', { value: parseInt(speedSlider.value) }); });

var joystickContainer = document.getElementById('joystick-container');
var joystickKnob = document.getElementById('joystick-knob');
var joystickLabel = document.getElementById('joystick-label');
var joystickDragging = false;
var joystickRafId = null;

function getJoystickCenter() {
  var rect = joystickContainer.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, r: rect.width / 2 - 24 };
}

function getDirection(dx, dy) {
  var angle = Math.atan2(-dy, dx) * 180 / Math.PI;
  var dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 10) return 'stop';
  if (angle > -22.5 && angle <= 22.5) return 'right';
  if (angle > 22.5 && angle <= 67.5) return 'forward_right';
  if (angle > 67.5 && angle <= 112.5) return 'forward';
  if (angle > 112.5 && angle <= 157.5) return 'forward_left';
  if (angle > 157.5 || angle <= -157.5) return 'left';
  if (angle > -157.5 && angle <= -112.5) return 'backward_left';
  if (angle > -112.5 && angle <= -67.5) return 'backward';
  if (angle > -67.5 && angle <= -22.5) return 'backward_right';
  return 'stop';
}

var dirLabels = {
  forward: 'Forward', backward: 'Backward', left: 'Left', right: 'Right',
  forward_left: 'Fwd-Left', forward_right: 'Fwd-Right',
  backward_left: 'Back-Left', backward_right: 'Back-Right', stop: 'Stopped'
};

function updateJoystick(clientX, clientY) {
  var center = getJoystickCenter();
  var dx = clientX - center.x;
  var dy = clientY - center.y;
  var dist = Math.sqrt(dx * dx + dy * dy);
  var maxR = center.r;
  if (dist > maxR) { dx = dx / dist * maxR; dy = dy / dist * maxR; }
  joystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  var dir = getDirection(dx, dy);
  joystickLabel.textContent = dirLabels[dir] || dir;
  var now = Date.now();
  if (dir !== lastSentDir || now - moveThrottle > 150) {
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
    moveThrottle = now;
  }
}

function moveKnobToDirection(dir) {
  var center = getJoystickCenter();
  var dist = center.r * 0.7;
  var dx = 0, dy = 0;
  switch (dir) {
    case 'forward':         dy = -dist; break;
    case 'backward':        dy = dist; break;
    case 'left':            dx = -dist; break;
    case 'right':           dx = dist; break;
    case 'forward_left':    dx = -dist * 0.7; dy = -dist * 0.7; break;
    case 'forward_right':   dx = dist * 0.7;  dy = -dist * 0.7; break;
    case 'backward_left':   dx = -dist * 0.7; dy = dist * 0.7;  break;
    case 'backward_right':  dx = dist * 0.7;  dy = dist * 0.7;  break;
    default: break;
  }
  joystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  joystickKnob.classList.add('dragging');
}

function resetJoystick() {
  joystickKnob.classList.add('spring-back');
  joystickKnob.style.transform = 'translate(-50%, -50%)';
  joystickLabel.textContent = 'Wheels — WASD';
  sendCommand('move', { dir: 'stop' });
  lastSentDir = 'stop';
  setTimeout(function() { joystickKnob.classList.remove('spring-back'); joystickKnob.classList.remove('dragging'); }, 300);
}

joystickKnob.addEventListener('pointerdown', function(e) {
  e.preventDefault();
  joystickDragging = true;
  joystickKnob.classList.add('dragging');
  joystickKnob.setPointerCapture(e.pointerId);
});
document.addEventListener('pointermove', function(e) {
  if (!joystickDragging) return;
  if (joystickRafId) cancelAnimationFrame(joystickRafId);
  joystickRafId = requestAnimationFrame(function() { updateJoystick(e.clientX, e.clientY); });
});
document.addEventListener('pointerup', function() {
  if (!joystickDragging) return;
  joystickDragging = false;
  resetJoystick();
});
document.addEventListener('pointercancel', function() {
  if (!joystickDragging) return;
  joystickDragging = false;
  resetJoystick();
});

var keysDown = {};

function wasdGetDirection() {
  var w = keysDown['w'];
  var a = keysDown['a'];
  var s = keysDown['s'];
  var d = keysDown['d'];
  if (w && a) return 'forward_left';
  if (w && d) return 'forward_right';
  if (s && a) return 'backward_left';
  if (s && d) return 'backward_right';
  if (w) return 'forward';
  if (s) return 'backward';
  if (a) return 'left';
  if (d) return 'right';
  return 'stop';
}

function wasdUpdate() {
  var dir = wasdGetDirection();
  if (dir !== lastSentDir) {
    moveKnobToDirection(dir);
    joystickLabel.textContent = dirLabels[dir] || dir;
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
    moveThrottle = Date.now();
  }
  if (dir === 'stop') {
    resetJoystick();
  }
}

var arrowKeysDown = {};
var CAM_ARROW_STEP = 5;
var CAM_ARROW_REPEAT = 80;
var camArrowTimer = null;

function arrowCamUpdate() {
  var up    = arrowKeysDown['arrowup'];
  var down  = arrowKeysDown['arrowdown'];
  var left  = arrowKeysDown['arrowleft'];
  var right = arrowKeysDown['arrowright'];

  var newPan = camPanAngle;
  var newTilt = camTiltAngle;
  if (left)  newPan  = Math.max(0,  camPanAngle  - CAM_ARROW_STEP);
  if (right) newPan  = Math.min(180, camPanAngle  + CAM_ARROW_STEP);
  if (up)    newTilt = Math.max(0,  camTiltAngle - CAM_ARROW_STEP);
  if (down)  newTilt = Math.min(180, camTiltAngle + CAM_ARROW_STEP);

  if (newPan !== camPanAngle || newTilt !== camTiltAngle) {
    camPanAngle = newPan;
    camTiltAngle = newTilt;
    var now = Date.now();
    if (now - camThrottle > 60) {
      sendCommand('servo', { id: 1, angle: camPanAngle });
      sendCommand('servo', { id: 2, angle: camTiltAngle });
      camThrottle = now;
      var panSlider = servoGrid.querySelector('[data-servo="1"]');
      var tiltSlider = servoGrid.querySelector('[data-servo="2"]');
      if (panSlider) { panSlider.value = camPanAngle; document.getElementById('sv-1').textContent = camPanAngle + '\u00B0'; }
      if (tiltSlider) { tiltSlider.value = camTiltAngle; document.getElementById('sv-2').textContent = camTiltAngle + '\u00B0'; }
    }
    moveCamKnobToAngles(camPanAngle, camTiltAngle);
    camJoystickLabel.textContent = 'Pan:' + camPanAngle + '\u00B0 Tilt:' + camTiltAngle + '\u00B0';
  }

  if (!up && !down && !left && !right) {
    if (camArrowTimer) { clearInterval(camArrowTimer); camArrowTimer = null; }
  }
}

function moveCamKnobToAngles(pan, tilt) {
  var center = getCamJoystickCenter();
  var maxR = center.r;
  var dx = ((pan - 90) / 90) * maxR;
  var dy = ((tilt - 90) / 90) * maxR;
  camJoystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  camJoystickKnob.classList.add('dragging');
}

var WASD_CODES = ['keyw','keya','keys','keyd'];
var ARROW_CODES = ['arrowup','arrowdown','arrowleft','arrowright'];

document.addEventListener('keydown', function(e) {
  var code = e.code.toLowerCase();
  if (WASD_CODES.indexOf(code) === -1 && ARROW_CODES.indexOf(code) === -1) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  e.preventDefault();

  if (WASD_CODES.indexOf(code) !== -1) {
    var key = code.replace('key', '');
    keysDown[key] = true;
    wasdUpdate();
  }

  if (ARROW_CODES.indexOf(code) !== -1) {
    if (!arrowKeysDown[code]) {
      arrowKeysDown[code] = true;
      arrowCamUpdate();
      if (!camArrowTimer) {
        camArrowTimer = setInterval(arrowCamUpdate, CAM_ARROW_REPEAT);
      }
    }
  }
});

document.addEventListener('keyup', function(e) {
  var code = e.code.toLowerCase();
  if (WASD_CODES.indexOf(code) === -1 && ARROW_CODES.indexOf(code) === -1) return;

  if (WASD_CODES.indexOf(code) !== -1) {
    var key = code.replace('key', '');
    delete keysDown[key];
    wasdUpdate();
  }

  if (ARROW_CODES.indexOf(code) !== -1) {
    delete arrowKeysDown[code];
    var anyArrow = arrowKeysDown['arrowup'] || arrowKeysDown['arrowdown'] || arrowKeysDown['arrowleft'] || arrowKeysDown['arrowright'];
    if (!anyArrow) {
      if (camArrowTimer) { clearInterval(camArrowTimer); camArrowTimer = null; }
      camJoystickKnob.classList.remove('dragging');
      camJoystickLabel.textContent = 'Pan:' + camPanAngle + '\u00B0 Tilt:' + camTiltAngle + '\u00B0';
    }
  }
});

var servoGrid = document.getElementById('servo-grid');
var servoValues = {};

servoDefs.forEach(function(sd) {
  servoValues[sd.id] = sd.init;
  var item = document.createElement('div');
  item.className = 'servo-item';
  item.setAttribute('data-servo-item', sd.id);
  item.innerHTML =
    '<label>' + sd.name + ' <span class="val" id="sv-' + sd.id + '">' + sd.init + '\u00B0</span></label>' +
    '<div class="servo-limits-row">' +
      '<label class="servo-limit-label">Min</label>' +
      '<input type="number" class="servo-limit-input" id="sv-min-' + sd.id + '" value="' + sd.min + '" data-servo-limit-min="' + sd.id + '">' +
      '<label class="servo-limit-label">Max</label>' +
      '<input type="number" class="servo-limit-input" id="sv-max-' + sd.id + '" value="' + sd.max + '" data-servo-limit-max="' + sd.id + '">' +
    '</div>' +
    '<input type="range" min="' + sd.min + '" max="' + sd.max + '" value="' + sd.init + '" data-servo="' + sd.id + '">';
  servoGrid.appendChild(item);
});

function applyServoLimits(limits) {
  if (!limits) return;
  Object.keys(limits).forEach(function(key) {
    var sid = parseInt(key);
    var lim = limits[key];
    var minInput = document.getElementById('sv-min-' + sid);
    var maxInput = document.getElementById('sv-max-' + sid);
    var slider = servoGrid.querySelector('[data-servo="' + sid + '"]');
    if (minInput && lim.min !== undefined) minInput.value = lim.min;
    if (maxInput && lim.max !== undefined) maxInput.value = lim.max;
    if (slider) {
      slider.min = lim.min;
      slider.max = lim.max;
    }
    var sd = servoDefs.find(function(s) { return s.id === sid; });
    if (sd) {
      sd.min = lim.min;
      sd.max = lim.max;
    }
  });
}

servoGrid.addEventListener('input', function(e) {
  if (e.target.dataset.servo === undefined) return;
  var idx = parseInt(e.target.dataset.servo);
  var val = parseInt(e.target.value);
  var sd = servoDefs.find(function(s) { return s.id === idx; });
  if (sd) val = Math.max(sd.min, Math.min(sd.max, val));
  servoValues[idx] = val;
  document.getElementById('sv-' + idx).textContent = val + '\u00B0';
});

servoGrid.addEventListener('change', function(e) {
  if (e.target.dataset.servo === undefined) return;
  var idx = parseInt(e.target.dataset.servo);
  sendCommand('servo', { id: idx, angle: servoValues[idx] });
});

servoGrid.addEventListener('change', function(e) {
  if (e.target.dataset.servoLimitMin !== undefined) {
    var sid = parseInt(e.target.dataset.servoLimitMin);
    var minVal = parseInt(e.target.value) || 0;
    var maxInput = document.getElementById('sv-max-' + sid);
    var maxVal = maxInput ? parseInt(maxInput.value) : 180;
    if (minVal > maxVal) { minVal = maxVal; e.target.value = minVal; }
    var slider = servoGrid.querySelector('[data-servo="' + sid + '"]');
    if (slider) slider.min = minVal;
    var sd = servoDefs.find(function(s) { return s.id === sid; });
    if (sd) sd.min = minVal;
    sendCommand('servo_set_limits', { id: sid, min: minVal, max: maxVal });
  }
  if (e.target.dataset.servoLimitMax !== undefined) {
    var sid = parseInt(e.target.dataset.servoLimitMax);
    var maxVal = parseInt(e.target.value) || 180;
    var minInput = document.getElementById('sv-min-' + sid);
    var minVal = minInput ? parseInt(minInput.value) : 0;
    if (maxVal < minVal) { maxVal = minVal; e.target.value = maxVal; }
    var slider = servoGrid.querySelector('[data-servo="' + sid + '"]');
    if (slider) slider.max = maxVal;
    var sd = servoDefs.find(function(s) { return s.id === sid; });
    if (sd) sd.max = maxVal;
    sendCommand('servo_set_limits', { id: sid, min: minVal, max: maxVal });
  }
});

document.getElementById('servo-home').addEventListener('click', function() {
  servoDefs.forEach(function(sd) {
    servoValues[sd.id] = sd.init;
    var slider = servoGrid.querySelector('[data-servo="' + sd.id + '"]');
    if (slider) slider.value = sd.init;
    document.getElementById('sv-' + sd.id).textContent = sd.init + '\u00B0';
  });
  sendCommand('servo_home', {});
  camPanAngle = 90;
  camTiltAngle = 90;
  craneArmClosed = false;
  craneGripPosition = 'high';
  updateCraneArmUI();
  updateCraneGripUI();
  camJoystickKnob.classList.add('spring-back');
  camJoystickKnob.style.transform = 'translate(-50%, -50%)';
  camJoystickLabel.textContent = 'Camera \u2014 Arrows';
  setTimeout(function() { camJoystickKnob.classList.remove('spring-back'); camJoystickKnob.classList.remove('dragging'); }, 300);
});

document.getElementById('servo-save-limits').addEventListener('click', function() {
  servoDefs.forEach(function(sd) {
    var minInput = document.getElementById('sv-min-' + sd.id);
    var maxInput = document.getElementById('sv-max-' + sd.id);
    if (minInput && maxInput) {
      sendCommand('servo_set_limits', { id: sd.id, min: parseInt(minInput.value), max: parseInt(maxInput.value) });
    }
  });
  toast('Servo limits saved', 'success');
});

var camJoystickContainer = document.getElementById('cam-joystick-container');
var camJoystickKnob = document.getElementById('cam-joystick-knob');
var camJoystickLabel = document.getElementById('cam-joystick-label');
var camJoystickDragging = false;
var camJoystickRafId = null;
var camPanAngle = 90;
var camTiltAngle = 90;
var camThrottle = 0;

function getCamJoystickCenter() {
  var rect = camJoystickContainer.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, r: rect.width / 2 - 21 };
}

function updateCamJoystick(clientX, clientY) {
  var center = getCamJoystickCenter();
  var dx = clientX - center.x;
  var dy = clientY - center.y;
  var dist = Math.sqrt(dx * dx + dy * dy);
  var maxR = center.r;
  if (dist > maxR) { dx = dx / dist * maxR; dy = dy / dist * maxR; }
  camJoystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  var panRange = 90;
  var tiltRange = 90;
  var newPan = Math.round(90 + (dx / maxR) * panRange);
  var newTilt = Math.round(90 + (dy / maxR) * tiltRange);
  newPan = Math.max(0, Math.min(180, newPan));
  newTilt = Math.max(0, Math.min(180, newTilt));
  camJoystickLabel.textContent = 'Pan:' + newPan + '\u00B0 Tilt:' + newTilt + '\u00B0';
  var now = Date.now();
  if ((newPan !== camPanAngle || newTilt !== camTiltAngle) && now - camThrottle > 100) {
    sendCommand('servo', { id: 1, angle: newPan });
    sendCommand('servo', { id: 2, angle: newTilt });
    camPanAngle = newPan;
    camTiltAngle = newTilt;
    camThrottle = now;
    var panSlider = servoGrid.querySelector('[data-servo="1"]');
    var tiltSlider = servoGrid.querySelector('[data-servo="2"]');
    if (panSlider) { panSlider.value = newPan; document.getElementById('sv-1').textContent = newPan + '\u00B0'; }
    if (tiltSlider) { tiltSlider.value = newTilt; document.getElementById('sv-2').textContent = newTilt + '\u00B0'; }
  }
}

function resetCamJoystick() {
  camJoystickKnob.classList.add('spring-back');
  camJoystickKnob.style.transform = 'translate(-50%, -50%)';
  camJoystickLabel.textContent = 'Camera \u2014 Arrows';
  setTimeout(function() { camJoystickKnob.classList.remove('spring-back'); camJoystickKnob.classList.remove('dragging'); }, 300);
}

camJoystickKnob.addEventListener('pointerdown', function(e) {
  e.preventDefault();
  camJoystickDragging = true;
  camJoystickKnob.classList.add('dragging');
  camJoystickKnob.setPointerCapture(e.pointerId);
});
document.addEventListener('pointermove', function(e) {
  if (!camJoystickDragging) return;
  if (camJoystickRafId) cancelAnimationFrame(camJoystickRafId);
  camJoystickRafId = requestAnimationFrame(function() { updateCamJoystick(e.clientX, e.clientY); });
});
document.addEventListener('pointerup', function() {
  if (!camJoystickDragging) return;
  camJoystickDragging = false;
  camJoystickKnob.classList.remove('dragging');
});
document.addEventListener('pointercancel', function() {
  if (!camJoystickDragging) return;
  camJoystickDragging = false;
  camJoystickKnob.classList.remove('dragging');
});

function updateHeadlightUI() {
  document.getElementById('hl-left').className = 'headlight-btn off';
  document.getElementById('hl-right').className = 'headlight-btn off';
  document.getElementById('hl-both').className = 'headlight-btn off';
  document.getElementById('hl-main').className = 'headlight-btn ' + (hlMainOn ? 'on' : 'off');
  document.getElementById('hl-left-signal').className = 'headlight-btn ' + (hlLeftSignal ? 'on' : 'off');
  document.getElementById('hl-right-signal').className = 'headlight-btn ' + (hlRightSignal ? 'on' : 'off');
}

document.getElementById('hl-main').addEventListener('click', function() {
  hlMainOn = !hlMainOn;
  sendCommand('headlight', { action: hlMainOn ? 'on' : 'off' });
  updateHeadlightUI();
});

document.getElementById('hl-left-signal').addEventListener('click', function() {
  hlLeftSignal = !hlLeftSignal;
  sendCommand('blinker', { side: 'left', active: hlLeftSignal });
  updateHeadlightUI();
});

document.getElementById('hl-right-signal').addEventListener('click', function() {
  hlRightSignal = !hlRightSignal;
  sendCommand('blinker', { side: 'right', active: hlRightSignal });
  updateHeadlightUI();
});

document.getElementById('hl-both-signal').addEventListener('click', function() {
  hlLeftSignal = false;
  hlRightSignal = false;
  sendCommand('blinker', { side: 'both_off' });
  updateHeadlightUI();
});

document.getElementById('hl-left').addEventListener('click', function() {
  var currentState = this.classList.contains('on');
  sendCommand('switch', { id: 0, state: !currentState });
  this.className = 'headlight-btn ' + (!currentState ? 'on' : 'off');
});
document.getElementById('hl-right').addEventListener('click', function() {
  var currentState = this.classList.contains('on');
  sendCommand('switch', { id: 1, state: !currentState });
  this.className = 'headlight-btn ' + (!currentState ? 'on' : 'off');
});
document.getElementById('hl-both').addEventListener('click', function() {
  var bothOn = document.getElementById('hl-left').classList.contains('on') && document.getElementById('hl-right').classList.contains('on');
  var ns = !bothOn;
  sendCommand('switch', { id: 0, state: ns }); sendCommand('switch', { id: 1, state: ns });
  document.getElementById('hl-left').className = 'headlight-btn ' + (ns ? 'on' : 'off');
  document.getElementById('hl-right').className = 'headlight-btn ' + (ns ? 'on' : 'off');
  this.className = 'headlight-btn ' + (ns ? 'on' : 'off');
});

function hexToRgb(hex) {
  return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
}

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

function updateCraneArmUI() {
  var openBtn = document.getElementById('crane-arm-open');
  var closeBtn = document.getElementById('crane-arm-close');
  if (!openBtn || !closeBtn) return;
  if (craneArmClosed) {
    closeBtn.classList.add('active');
    openBtn.classList.remove('active');
  } else {
    openBtn.classList.add('active');
    closeBtn.classList.remove('active');
  }
}

function updateCraneGripUI() {
  var lowBtn = document.getElementById('crane-grip-low');
  var midBtn = document.getElementById('crane-grip-mid');
  var highBtn = document.getElementById('crane-grip-high');
  var label = document.getElementById('crane-grip-label');
  if (!lowBtn) return;
  lowBtn.classList.remove('active');
  midBtn.classList.remove('active');
  highBtn.classList.remove('active');
  var angleMap = { low: 0, mid: 135, high: 190 };
  var labelMap = { low: 'Low', mid: 'Mid', high: 'High' };
  if (craneGripPosition === 'low') lowBtn.classList.add('active');
  else if (craneGripPosition === 'mid') midBtn.classList.add('active');
  else highBtn.classList.add('active');
  if (label) label.textContent = labelMap[craneGripPosition] + ' (' + angleMap[craneGripPosition] + '\u00B0)';
}

document.querySelectorAll('.crane-arm-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var action = btn.dataset.action;
    if (action === 'arm_close') craneArmClosed = true;
    else if (action === 'arm_open') craneArmClosed = false;
    sendCommand('crane', { action: action });
    updateCraneArmUI();
  });
});

document.querySelectorAll('.crane-grip-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var action = btn.dataset.action;
    if (action === 'grip_low') craneGripPosition = 'low';
    else if (action === 'grip_mid') craneGripPosition = 'mid';
    else if (action === 'grip_high') craneGripPosition = 'high';
    sendCommand('crane', { action: action });
    updateCraneGripUI();
  });
});

updateCraneArmUI();
updateCraneGripUI();

document.getElementById('i2c-scan-btn').addEventListener('click', function() {
  var resultEl = document.getElementById('i2c-scan-result');
  resultEl.textContent = 'Scanning...';
  resultEl.style.color = '#fdd663';
  sendCommand('i2c_scan', {});
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    fetch('/api/i2c_scan').then(function(r) { return r.json(); }).then(function(d) {
      showI2CResult(d, resultEl);
    }).catch(function() { resultEl.textContent = 'Scan failed'; resultEl.style.color = '#ea4335'; });
  }
});

function showI2CResult(d, el) {
  if (d.mpu6050_found) {
    el.textContent = 'MPU6050 at ' + d.mpu6050_addr + ' (WHO_AM_I=' + d.mpu6050_who_am_i + ')';
    el.style.color = '#34a853';
  } else if (d.devices && d.devices.length > 0) {
    el.textContent = 'No MPU6050. Devices: ' + d.devices.join(', ');
    el.style.color = '#ea4335';
  } else {
    el.textContent = 'No I2C devices found!';
    el.style.color = '#ea4335';
  }
}

document.querySelectorAll('#auto-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#auto-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    if (btn.dataset.auto !== 'stop') btn.classList.add('active');
    sendCommand('auto', { func: btn.dataset.auto });
  });
});

var voiceActive = false;
var voiceAvailable = false;

function updateVoiceUI() {
  var startBtn = document.getElementById('voice-start-btn');
  var stopBtn = document.getElementById('voice-stop-btn');
  var dot = document.getElementById('voice-status-dot');
  var txt = document.getElementById('voice-status-text');
  if (!startBtn) return;
  if (voiceActive) {
    startBtn.style.display = 'none';
    stopBtn.style.display = '';
    dot.style.background = '#34a853';
    txt.textContent = 'Listening...';
    txt.style.color = '#34a853';
  } else {
    startBtn.style.display = '';
    stopBtn.style.display = 'none';
    if (voiceAvailable) {
      dot.style.background = '#9aa0a6';
      txt.textContent = 'Inactive';
      txt.style.color = '#9aa0a6';
    } else {
      dot.style.background = '#ea4335';
      txt.textContent = 'Not available';
      txt.style.color = '#ea4335';
    }
  }
}

document.getElementById('voice-start-btn').addEventListener('click', function() {
  sendCommand('voice', { action: 'start' });
  voiceActive = true;
  updateVoiceUI();
});

document.getElementById('voice-stop-btn').addEventListener('click', function() {
  sendCommand('voice', { action: 'stop' });
  voiceActive = false;
  updateVoiceUI();
});

var docsData = null;
var docsLoaded = false;
var currentDocPage = 'overview';

async function loadDocs() {
  if (docsLoaded) return;
  var main = document.getElementById('info-main');
  main.innerHTML = '<div class="info-loading">Loading documentation...</div>';

  try {
    var [indexRes, pinoutRes] = await Promise.all([
      fetch('/docs/index.json').then(function(r) { return r.json(); }),
      fetch('/docs/pinout.json').then(function(r) { return r.json(); })
    ]);

    var compFetches = (indexRes.components || []).concat(indexRes.additional_hardware || []).map(function(c) {
      if (c.documentation_path) {
        return fetch('/' + c.documentation_path).then(function(r) { return r.json(); }).then(function(d) {
          return { id: c.id, name: c.name, data: d };
        }).catch(function() { return null; });
      }
      return Promise.resolve(null);
    });

    var compResults = await Promise.all(compFetches);
    var components = {};
    compResults.forEach(function(r) { if (r) components[r.id] = r; });

    docsData = { index: indexRes, pinout: pinoutRes, components: components };
    docsLoaded = true;
    showDocPage(currentDocPage);
  } catch(e) {
    main.innerHTML = '<div class="info-loading">Failed to load docs</div>';
  }
}

function showDocPage(page) {
  currentDocPage = page;
  var main = document.getElementById('info-main');
  if (!docsData) return;

  document.querySelectorAll('.info-nav-btn, .info-comp-btn').forEach(function(b) { b.classList.remove('active'); });
  var activeBtn = document.querySelector('[data-doc="' + page + '"]');
  if (activeBtn) activeBtn.classList.add('active');

  if (page === 'overview') {
    var idx = docsData.index;
    var html = '<h2 class="info-title">' + (idx.project_name || 'PiCar Pro') + '</h2>';
    html += '<p class="info-subtitle">' + (idx.description || '') + '</p>';
    if (idx.features) {
      html += '<div class="info-section"><h3 class="info-section-title">Features</h3>';
      idx.features.forEach(function(f) {
        html += '<div class="info-field"><span class="info-field-label">' + f.name + '</span><span class="info-field-value">' + f.description + '</span></div>';
      });
      html += '</div>';
    }
    if (idx.components) {
      html += '<div class="info-section"><h3 class="info-section-title">Components</h3>';
      idx.components.forEach(function(c) {
        html += '<div class="info-field"><span class="info-field-label">' + c.name + '</span><span class="info-field-value">' + (c.description || '') + '</span></div>';
      });
      html += '</div>';
    }
    main.innerHTML = html;
  } else if (page === 'pinout') {
    var pin = docsData.pinout;
    var html = '<h2 class="info-title">GPIO Pinout</h2>';
    html += '<p class="info-subtitle">Raspberry Pi GPIO assignments for PiCar Pro</p>';
    html += '<div style="margin-bottom:16px;text-align:center"><img src="/dist/rpi_pinout.png" alt="Raspberry Pi Pinout" style="max-width:100%;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)"></div>';
    if (pin.pins) {
      html += '<table class="pin-table"><thead><tr><th>GPIO</th><th>Function</th><th>Component</th><th>Notes</th></tr></thead><tbody>';
      pin.pins.forEach(function(p) {
        html += '<tr><td><span class="pin-color" style="background:' + (p.color || '#dadce0') + '"></span>GPIO' + p.gpio + '</td><td>' + (p.function || '') + '</td><td>' + (p.component || '') + '</td><td>' + (p.notes || '') + '</td></tr>';
      });
      html += '</tbody></table>';
    }
    if (pin.conflicts && pin.conflicts.length > 0) {
      html += '<div class="info-section" style="margin-top:16px"><h3 class="info-section-title">Conflicts</h3>';
      pin.conflicts.forEach(function(c) {
        html += '<div class="info-field"><span class="pin-conflict">' + c.gpio + '</span><span class="info-field-value">' + c.description + '</span></div>';
      });
      html += '</div>';
    }
    main.innerHTML = html;
  } else if (docsData.components[page]) {
    var comp = docsData.components[page];
    var d = comp.data;
    var html = '<h2 class="info-title">' + comp.name + '</h2>';
    if (d.description) html += '<p class="info-subtitle">' + d.description + '</p>';
    if (d.specs) {
      html += '<div class="info-section"><h3 class="info-section-title">Specifications</h3><div class="specs-grid">';
      d.specs.forEach(function(s) {
        html += '<div class="spec-item"><span class="spec-key">' + s.key + '</span><span class="spec-val">' + s.value + '</span></div>';
      });
      html += '</div></div>';
    }
    if (d.pins) {
      html += '<div class="info-section"><h3 class="info-section-title">Pin Connections</h3><div class="comp-pins-list">';
      d.pins.forEach(function(p) {
        html += '<div class="comp-pin"><div class="comp-pin-name">' + p.name + '</div><div class="comp-pin-conn">' + (p.connection || '') + '</div><div class="comp-pin-func">' + (p.function || '') + '</div></div>';
      });
      html += '</div></div>';
    }
    if (d.tips) {
      html += '<div class="info-section"><h3 class="info-section-title">Tips</h3><ul class="tips-list">';
      d.tips.forEach(function(t) { html += '<li>' + t + '</li>'; });
      html += '</ul></div>';
    }
    if (d.i2c_address) {
      html += '<div class="info-section"><h3 class="info-section-title">I2C Info</h3><div class="i2c-device-grid"><div class="i2c-device"><div class="i2c-device-addr">0x' + d.i2c_address.toString(16).toUpperCase() + '</div><div class="i2c-device-name">' + comp.name + '</div></div></div></div>';
    }
    main.innerHTML = html;
  }
}

function populateComponentNav() {
  var nav = document.getElementById('info-component-nav');
  if (!nav || !docsData) return;
  nav.innerHTML = '';
  var allComps = (docsData.index.components || []).concat(docsData.index.additional_hardware || []);
  allComps.forEach(function(c) {
    var btn = document.createElement('button');
    btn.className = 'info-comp-btn';
    btn.dataset.doc = c.id;
    btn.textContent = c.name;
    btn.addEventListener('click', function() { showDocPage(c.id); });
    nav.appendChild(btn);
  });
}

var origLoadDocs = loadDocs;
loadDocs = async function() {
  await origLoadDocs();
  populateComponentNav();
};

document.querySelectorAll('.info-nav-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    showDocPage(btn.dataset.doc);
  });
});

var consoleAutoScroll = true;
var consoleLineCount = 0;

function getLogLevel(text) {
  if (!text) return 'debug';
  var t = text.toUpperCase();
  if (t.indexOf('| ERROR') !== -1 || t.indexOf('[ERROR]') !== -1) return 'error';
  if (t.indexOf('| WARNING') !== -1 || t.indexOf('| WARN') !== -1 || t.indexOf('[WARN') !== -1) return 'warn';
  if (t.indexOf('| DEBUG') !== -1 || t.indexOf('[DEBUG]') !== -1) return 'debug';
  if (t.indexOf('| INFO') !== -1 || t.indexOf('[INFO]') !== -1) return 'info';
  return 'info';
}

function appendConsoleLine(text, ts) {
  var output = document.getElementById('console-output');
  if (!output) return;
  var level = getLogLevel(text);
  logCounts[level] = (logCounts[level] || 0) + 1;
  updateLogCounters();

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
  logLines.push({ el: line, level: level, ts: ts || Date.now() / 1000 });
  consoleLineCount++;
  var countEl = document.getElementById('console-line-count');
  if (countEl) countEl.textContent = consoleLineCount + ' lines';
  if (consoleAutoScroll) output.scrollTop = output.scrollHeight;
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
  }
  lines.forEach(function(line) { output.appendChild(line); });
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
  consoleLineCount = 0;
  logCounts = { info: 0, warn: 0, error: 0, debug: 0 };
  logLines = [];
  updateLogCounters();
  var countEl = document.getElementById('console-line-count');
  if (countEl) countEl.textContent = '0 lines';
  sendCommand('clear_log', {});
});

var btScanBtn = document.getElementById('bt-scan-btn');
var btAutoBtn = document.getElementById('bt-auto-btn');
var btDisconnectBtn = document.getElementById('bt-disconnect-btn');
var btDeviceList = document.getElementById('bt-device-list');
var btDevices = document.getElementById('bt-devices');
var btScanning = document.getElementById('bt-scanning');
var btSavedInfo = document.getElementById('bt-saved-info');

btScanBtn.addEventListener('click', function() {
  btScanning.style.display = '';
  btDeviceList.style.display = 'none';
  btDevices.innerHTML = '';
  fetch('/api/bt/scan').then(function(r) { return r.json(); }).then(function(d) {
    btScanning.style.display = 'none';
    if (d.devices && d.devices.length > 0) {
      btDeviceList.style.display = '';
      d.devices.forEach(function(dev) {
        var item = document.createElement('div');
        item.className = 'bt-device-item' + (dev.is_gamepad ? ' bt-gamepad' : '');
        item.innerHTML = '<span class="bt-device-name">' + dev.name + ' (' + dev.mac + ')</span>' +
          '<button class="btn-sm btn-primary bt-connect-btn" data-mac="' + dev.mac + '">Connect</button>';
        btDevices.appendChild(item);
        item.querySelector('.bt-connect-btn').addEventListener('click', function() {
          fetch('/api/bt/connect', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ mac: dev.mac }) }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) toast('Connected!', 'success');
            else toast(d.error || 'Connection failed', 'error');
          }).catch(function() { toast('Connection error', 'error'); });
        });
      });
    } else {
      btDeviceList.style.display = '';
      btDevices.innerHTML = '<div style="font-size:.75rem;color:#5f6368;padding:4px">No devices found</div>';
    }
  }).catch(function() {
    btScanning.style.display = 'none';
    toast('Scan failed', 'error');
  });
});

btAutoBtn.addEventListener('click', function() {
  fetch('/api/bt/auto_connect', { method: 'POST' }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast('Auto-connect started', 'success');
    else toast(d.error || 'Auto-connect failed', 'error');
  }).catch(function() { toast('Auto-connect error', 'error'); });
});

btDisconnectBtn.addEventListener('click', function() {
  fetch('/api/bt/disconnect', { method: 'POST' }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast('Disconnected', 'success');
    else toast(d.error || 'Disconnect failed', 'error');
  }).catch(function() { toast('Disconnect error', 'error'); });
});

fetch('/api/bt/status').then(function(r) { return r.json(); }).then(function(d) {
  if (d.saved_mac) {
    btSavedInfo.textContent = 'Saved: ' + d.saved_mac;
  }
}).catch(function() {});
