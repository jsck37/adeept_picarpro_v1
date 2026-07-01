var servoDefs = [
  { id: 0, name: 'Steering', min: 30, max: 150, init: 90 },
  { id: 1, name: 'Cam Pan',  min: 0,  max: 180, init: 90 },
  { id: 2, name: 'Cam Tilt', min: 0,  max: 180, init: 90 },
  { id: 3, name: 'Servo 3',  min: 0,  max: 180, init: 90 },
  { id: 4, name: 'Servo 4',  min: 0,  max: 180, init: 90 },
  { id: 5, name: 'Crane Grip', min: 0, max: 190, init: 190 },
  { id: 6, name: 'Crane Arm',  min: 0, max: 180, init: 80 },
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
var consoleAutoScroll = true;
var hw = {
  motors: false, servos: false, leds: false, buzzer: false,
  switches: false, ultrasonic: false, mpu6050: false,
  oled: false, camera: false, autonomous: false, crane: false,
  ds4: false, voice: false,
};
var docsData = null;
var ws = null;
var wsReconnectTimer = null;
var usePolling = false;
var pollTimer = null;

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
      'hand_color': '/cmd/hand_color', 'servo_get_limits': '/cmd/servo_get_limits',
      'servo_set_limits': '/cmd/servo_set_limits', 'i2c_scan': '/cmd/i2c_scan',
      'web_active': '/cmd/web_active', 'clear_log': '/cmd/clear_log',
    };
    var url = urlMap[cmd];
    if (url) {
      fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(params) }).catch(function() {});
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
  });
});

document.querySelectorAll('.tab-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    document.getElementById('content-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'info') loadDocs();
    if (btn.dataset.tab === 'console') fetchConsoleHistory();
  });
});

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

function wsConnect() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  var wsHost = location.hostname;
  var wsPort = 8888;
  var url = 'ws://' + wsHost + ':' + wsPort;
  try {
    ws = new WebSocket(url);
    ws.onopen = function() {
      document.getElementById('connection-dot').classList.remove('offline');
      usePolling = false;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        var msgType = data.type || '';
        var msgData = data.data || {};
        if (msgType === 'status') updateStatus(msgData);
        else if (msgType === 'log') appendConsoleLine(data.text, data.ts);
        else if (msgType === 'log_history') {
          if (data.lines && data.lines.length) {
            data.lines.forEach(function(item) { appendConsoleLine(item[1], item[0]); });
          }
        }
        else if (msgType === 'response') {
          if (msgData.error) toast(msgData.error, 'error');
          if (msgData.cmd === 'servo_get_limits' && msgData.ok) {
            applyServoLimits(msgData.limits);
          }
        }
      } catch(err) {}
    };
    ws.onclose = function() {
      document.getElementById('connection-dot').classList.add('offline');
      wsReconnectTimer = setTimeout(wsConnect, 3000);
      if (!usePolling) { usePolling = true; startPolling(); }
    };
    ws.onerror = function() { ws.close(); };
  } catch(e) { usePolling = true; startPolling(); }
}

function startPolling() {
  if (pollTimer) return;
  function poll() {
    fetch('/api/status').then(function(r) { return r.json(); }).then(function(d) {
      updateStatus(d);
    }).catch(function() {});
  }
  poll();
  pollTimer = setInterval(poll, 1500);
}

function fetchConsoleHistory() {
  fetch('/api/logs').then(function(r) { return r.json(); }).then(function(d) {
    if (d.lines) d.lines.forEach(function(item) { appendConsoleLine(item[1], item[0]); });
  }).catch(function() {});
}

var firstStatus = true;
function updateStatus(d) {
  if (!d) return;
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
  if (d.speed !== undefined) document.getElementById('sb-speed').textContent = d.speed + '%';
  var autoModeLabels = {
    'none': 'Ready', 'radarScan': 'Radar', 'automatic': 'Auto Drive',
    'trackLine': 'IR Line', 'trackLineCV': 'CV Line', 'trackHand': 'Hand Track',
    'keepDistance': 'Distance'
  };
  var sbModule = document.getElementById('sb-module');
  if (sbModule) sbModule.textContent = autoModeLabels[d.auto_mode || 'none'] || d.auto_mode || 'Ready';
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
  if (lvBanner) lvBanner.style.display = d.low_voltage ? 'block' : 'none';
  var ds4 = d.ds4;
  if (ds4) {
    document.getElementById('sb-ds4').textContent = ds4.connected ? 'ON' : '--';
    var ds4Dot = document.getElementById('ds4-status-dot');
    var ds4Text = document.getElementById('ds4-status-text');
    if (ds4Dot) ds4Dot.style.background = ds4.connected ? '#34a853' : '#ea4335';
    if (ds4Text) {
      ds4Text.textContent = ds4.connected ? 'Connected (' + ds4.connect_count + '\u00D7)' : 'Not connected';
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
    var badge = document.getElementById('cv-badge');
    if (d.cv_mode && d.cv_mode !== 'none') {
      badge.textContent = 'CV: ' + d.cv_mode;
      badge.classList.add('visible');
    } else {
      badge.classList.remove('visible');
    }
  }
  if (d.headlight !== undefined) {
    hlMainOn = d.headlight;
    var hlMainBtn = document.getElementById('hl-main');
    if (hlMainBtn) hlMainBtn.className = 'headlight-btn ' + (hlMainOn ? 'on' : 'off');
  }
  if (d.left_blinker !== undefined) {
    hlLeftSignal = d.left_blinker;
    document.getElementById('hl-left-signal').className = 'headlight-btn ' + (hlLeftSignal ? 'on' : 'off');
    var lStat = document.getElementById('blinker-left-status');
    if (lStat) { lStat.textContent = hlLeftSignal ? 'ON' : 'OFF'; lStat.style.color = hlLeftSignal ? '#fbbc04' : '#9aa0a6'; }
  }
  if (d.right_blinker !== undefined) {
    hlRightSignal = d.right_blinker;
    document.getElementById('hl-right-signal').className = 'headlight-btn ' + (hlRightSignal ? 'on' : 'off');
    var rStat = document.getElementById('blinker-right-status');
    if (rStat) { rStat.textContent = hlRightSignal ? 'ON' : 'OFF'; rStat.style.color = hlRightSignal ? '#fbbc04' : '#9aa0a6'; }
  }
  if (d.voice) {
    var v = d.voice;
    var vDot = document.getElementById('voice-status-dot');
    var vText = document.getElementById('voice-status-text');
    var vStart = document.getElementById('voice-start-btn');
    var vStop = document.getElementById('voice-stop-btn');
    if (vDot) vDot.style.background = v.active ? '#34a853' : (v.available ? '#fbbc04' : '#9aa0a6');
    if (vText) {
      vText.textContent = v.active ? 'Listening...' : (v.available ? 'Ready' : 'Inactive');
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

function updateCraneArmUI() {
  var toggleBtn = document.getElementById('crane-arm-toggle');
  if (!toggleBtn) return;
  if (craneArmClosed) {
    toggleBtn.classList.add('active');
    toggleBtn.textContent = 'Release';
  } else {
    toggleBtn.classList.remove('active');
    toggleBtn.textContent = 'Grab / Release';
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
  else if (craneGripPosition === 'high') highBtn.classList.add('active');
  if (label && angleMap[craneGripPosition] !== undefined) {
    label.textContent = labelMap[craneGripPosition] + ' (' + angleMap[craneGripPosition] + '\u00B0)';
  }
}

function buildServoGrid() {
  var grid = document.getElementById('servo-grid');
  if (!grid) return;
  servoDefs.forEach(function(s) {
    var item = document.createElement('div');
    item.className = 'servo-item';
    item.innerHTML = '<label>' + s.name + ' <span class="val">' + s.init + '\u00B0</span></label>' +
      '<input type="range" min="' + s.min + '" max="' + s.max + '" value="' + s.init + '" data-servo="' + s.id + '">' +
      '<div class="servo-limits-row"><span class="servo-limit-label">min</span>' +
      '<input type="number" class="servo-limit-input" min="0" max="180" value="' + s.min + '" data-servo-min="' + s.id + '">' +
      '<span class="servo-limit-label">max</span>' +
      '<input type="number" class="servo-limit-input" min="0" max="180" value="' + s.max + '" data-servo-max="' + s.id + '">' +
      '</div>';
    grid.appendChild(item);
    var slider = item.querySelector('input[type=range]');
    var valSpan = item.querySelector('.val');
    var minInput = item.querySelector('input[data-servo-min]');
    var maxInput = item.querySelector('input[data-servo-max]');
    slider.addEventListener('input', function() { valSpan.textContent = slider.value + '\u00B0'; });
    slider.addEventListener('change', function() {
      sendCommand('servo', { id: s.id, angle: parseInt(slider.value) });
    });
    minInput.addEventListener('change', function() {
      var mn = parseInt(minInput.value);
      var mx = parseInt(maxInput.value);
      if (mn < mx) {
        sendCommand('servo_set_limits', { id: s.id, min: mn, max: mx });
        slider.min = mn;
      }
    });
    maxInput.addEventListener('change', function() {
      var mn = parseInt(minInput.value);
      var mx = parseInt(maxInput.value);
      if (mn < mx) {
        sendCommand('servo_set_limits', { id: s.id, min: mn, max: mx });
        slider.max = mx;
      }
    });
  });
}

function applyServoLimits(limits) {
  for (var sid in limits) {
    var lim = limits[sid];
    var slider = document.querySelector('input[data-servo="' + sid + '"]');
    if (slider) {
      slider.min = lim.min;
      slider.max = lim.max;
    }
  }
}

var joystickContainer = document.getElementById('joystick-container');
var joystickKnob = document.getElementById('joystick-knob');
var joystickLabel = document.getElementById('joystick-label');
var joystickDragging = false;

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

function resetJoystick() {
  joystickKnob.classList.add('spring-back');
  joystickKnob.style.transform = 'translate(-50%, -50%)';
  joystickLabel.textContent = 'Wheels \u2014 WASD';
  setTimeout(function() { joystickKnob.classList.remove('spring-back'); }, 300);
  if (lastSentDir !== 'stop') {
    sendCommand('move', { dir: 'stop' });
    lastSentDir = 'stop';
  }
}

if (joystickContainer) {
  joystickContainer.addEventListener('mousedown', function(e) {
    e.preventDefault();
    joystickDragging = true;
    joystickKnob.classList.remove('spring-back');
    updateJoystick(e.clientX, e.clientY);
  });
  joystickContainer.addEventListener('touchstart', function(e) {
    e.preventDefault();
    joystickDragging = true;
    joystickKnob.classList.remove('spring-back');
    var t = e.touches[0];
    updateJoystick(t.clientX, t.clientY);
  }, { passive: false });
  document.addEventListener('mousemove', function(e) {
    if (joystickDragging) updateJoystick(e.clientX, e.clientY);
  });
  document.addEventListener('touchmove', function(e) {
    if (joystickDragging) {
      e.preventDefault();
      var t = e.touches[0];
      updateJoystick(t.clientX, t.clientY);
    }
  }, { passive: false });
  document.addEventListener('mouseup', function() { if (joystickDragging) { joystickDragging = false; resetJoystick(); } });
  document.addEventListener('touchend', function() { if (joystickDragging) { joystickDragging = false; resetJoystick(); } });
}

var camJoystickContainer = document.getElementById('cam-joystick-container');
var camJoystickKnob = document.getElementById('cam-joystick-knob');
var camJoystickLabel = document.getElementById('cam-joystick-label');
var camJoystickDragging = false;
var camPan = 90, camTilt = 90;

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
  var panDelta = Math.round(dx / maxR * 30);
  var tiltDelta = Math.round(dy / maxR * 30);
  var newPan = Math.max(0, Math.min(180, 90 + panDelta));
  var newTilt = Math.max(0, Math.min(180, 90 + tiltDelta));
  if (newPan !== camPan) {
    sendCommand('servo', { id: 1, angle: newPan });
    camPan = newPan;
  }
  if (newTilt !== camTilt) {
    sendCommand('servo', { id: 2, angle: newTilt });
    camTilt = newTilt;
  }
  if (camJoystickLabel) {
    if (Math.abs(dx) < 5 && Math.abs(dy) < 5) {
      camJoystickLabel.textContent = 'Camera \u2014 Arrows';
    } else {
      camJoystickLabel.textContent = 'Pan:' + newPan + '\u00B0 Tilt:' + newTilt + '\u00B0';
    }
  }
}

function resetCamJoystick() {
  camJoystickKnob.classList.add('spring-back');
  camJoystickKnob.style.transform = 'translate(-50%, -50%)';
  if (camJoystickLabel) camJoystickLabel.textContent = 'Camera \u2014 Arrows';
  setTimeout(function() { camJoystickKnob.classList.remove('spring-back'); }, 300);
}

if (camJoystickContainer) {
  camJoystickContainer.addEventListener('mousedown', function(e) {
    e.preventDefault();
    camJoystickDragging = true;
    camJoystickKnob.classList.remove('spring-back');
    updateCamJoystick(e.clientX, e.clientY);
  });
  camJoystickContainer.addEventListener('touchstart', function(e) {
    e.preventDefault();
    camJoystickDragging = true;
    camJoystickKnob.classList.remove('spring-back');
    var t = e.touches[0];
    updateCamJoystick(t.clientX, t.clientY);
  }, { passive: false });
  document.addEventListener('mousemove', function(e) {
    if (camJoystickDragging) updateCamJoystick(e.clientX, e.clientY);
  });
  document.addEventListener('touchmove', function(e) {
    if (camJoystickDragging) {
      e.preventDefault();
      var t = e.touches[0];
      updateCamJoystick(t.clientX, t.clientY);
    }
  }, { passive: false });
  document.addEventListener('mouseup', function() { if (camJoystickDragging) { camJoystickDragging = false; resetCamJoystick(); } });
  document.addEventListener('touchend', function() { if (camJoystickDragging) { camJoystickDragging = false; resetCamJoystick(); } });
}

var keyMap = {
  'w': 'forward', 's': 'backward', 'a': 'left', 'd': 'right',
  'ArrowUp': 'forward', 'ArrowDown': 'backward',
  'ArrowLeft': 'left', 'ArrowRight': 'right',
};
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  var dir = keyMap[e.key];
  if (dir && dir !== lastSentDir) {
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
  }
});
document.addEventListener('keyup', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (keyMap[e.key] && lastSentDir !== 'stop') {
    sendCommand('move', { dir: 'stop' });
    lastSentDir = 'stop';
  }
});

document.querySelectorAll('.cv-btn[data-cv]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.cv-btn[data-cv]').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var mode = btn.dataset.cv;
    var badge = document.getElementById('cv-badge');
    var handRow = document.getElementById('hand-color-row');
    if (mode === 'findlineCV') {
      badge.textContent = 'CV: Line Follow';
      badge.classList.add('visible');
      sendCommand('auto', { func: 'trackLineCV' });
      if (handRow) handRow.style.display = 'none';
    } else if (mode === 'trackHand') {
      badge.textContent = 'CV: Hand Track';
      badge.classList.add('visible');
      sendCommand('auto', { func: 'trackHand' });
      if (handRow) handRow.style.display = '';
    } else {
      badge.textContent = 'CV: ' + mode.charAt(0).toUpperCase() + mode.slice(1);
      badge.classList.toggle('visible', mode !== 'none');
      if (mode !== 'none') sendCommand('auto', { func: 'stop' });
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
    toast('Custom hand color applied', 'success');
  });
}

var speedSlider = document.getElementById('speed-slider');
var speedVal = document.getElementById('speed-val');
if (speedSlider) {
  speedSlider.addEventListener('input', function() { speedVal.textContent = speedSlider.value + '%'; });
  speedSlider.addEventListener('change', function() { sendCommand('speed', { value: parseInt(speedSlider.value) }); });
}

document.getElementById('servo-home').addEventListener('click', function() {
  sendCommand('servo_home', {});
  craneArmClosed = false;
  craneGripPosition = 'high';
  updateCraneArmUI();
  updateCraneGripUI();
});
document.getElementById('servo-save-limits').addEventListener('click', function() {
  sendCommand('servo_get_limits', {});
});

document.querySelectorAll('.crane-arm-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    craneArmClosed = !craneArmClosed;
    sendCommand('crane', { action: craneArmClosed ? 'arm_close' : 'arm_open' });
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

document.getElementById('hl-main').addEventListener('click', function() {
  hlMainOn = !hlMainOn;
  sendCommand('headlight', { action: hlMainOn ? 'on' : 'off' });
  this.className = 'headlight-btn ' + (hlMainOn ? 'on' : 'off');
});

document.getElementById('hl-left-signal').addEventListener('click', function() {
  hlLeftSignal = !hlLeftSignal;
  if (hlLeftSignal) {
    hlRightSignal = false;
    document.getElementById('hl-right-signal').className = 'headlight-btn off';
  }
  sendCommand('blinker', { side: 'left', active: hlLeftSignal });
  this.className = 'headlight-btn ' + (hlLeftSignal ? 'on' : 'off');
});

document.getElementById('hl-right-signal').addEventListener('click', function() {
  hlRightSignal = !hlRightSignal;
  if (hlRightSignal) {
    hlLeftSignal = false;
    document.getElementById('hl-left-signal').className = 'headlight-btn off';
  }
  sendCommand('blinker', { side: 'right', active: hlRightSignal });
  this.className = 'headlight-btn ' + (hlRightSignal ? 'on' : 'off');
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

document.querySelectorAll('#auto-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#auto-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    if (btn.dataset.auto !== 'stop') btn.classList.add('active');
    sendCommand('auto', { func: btn.dataset.auto });
  });
});

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
          var btn = this;
          btn.textContent = '...';
          fetch('/api/bt/connect', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ mac: dev.mac, name: dev.name }) }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) { toast('Connected!', 'success'); btn.textContent = 'Connected'; }
            else { toast(d.message || d.error || 'Connection failed', 'error'); btn.textContent = 'Connect'; }
          }).catch(function() { toast('Connection error', 'error'); btn.textContent = 'Connect'; });
        });
      });
    } else {
      btDeviceList.style.display = '';
      btDevices.innerHTML = '<div style="font-size:.75rem;color:#5f6368;padding:4px">No devices found</div>';
    }
  }).catch(function() {
    btScanning.style.display = 'none';
    btDeviceList.style.display = '';
    btDevices.innerHTML = '<div style="font-size:.75rem;color:#ea4335;padding:4px">Scan failed</div>';
  });
});

btAutoBtn.addEventListener('click', function() {
  fetch('/api/bt/auto_connect', { method: 'POST' }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast('Auto-connect started', 'success');
    else toast(d.error || d.message || 'Auto-connect failed', 'error');
  }).catch(function() { toast('Auto-connect error', 'error'); });
});

btDisconnectBtn.addEventListener('click', function() {
  fetch('/api/bt/disconnect', { method: 'POST' }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast('Disconnected', 'success');
    else toast(d.error || 'Disconnect failed', 'error');
  }).catch(function() { toast('Disconnect error', 'error'); });
});

fetch('/api/bt/status').then(function(r) { return r.json(); }).then(function(d) {
  if (d.saved_mac && btSavedInfo) {
    btSavedInfo.textContent = 'Saved: ' + d.saved_mac;
  }
}).catch(function() {});

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
  if (consoleAutoScroll) output.scrollTop = output.scrollHeight;
  var countEl = document.getElementById('console-line-count');
  if (countEl) countEl.textContent = output.children.length + ' lines';
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
  if (countEl) countEl.textContent = '0 lines';
  sendCommand('clear_log', {});
});

async function loadDocs() {
  if (docsData) { populateComponentNav(); showDocPage('overview'); return; }
  try {
    var [idx, pinout] = await Promise.all([
      fetch('/docs/index.json').then(function(r) { return r.json(); }),
      fetch('/docs/pinout.json').then(function(r) { return r.json(); }),
    ]);
    docsData = { index: idx, pinout: pinout, components: {} };
    var comps = (idx.components || []).concat(idx.additional_hardware || []);
    await Promise.all(comps.map(async function(c) {
      try {
        docsData.components[c.id] = await fetch('/docs/components/' + c.id + '.json').then(function(r) { return r.json(); });
      } catch (e) {}
    }));
    populateComponentNav();
    showDocPage('overview');
  } catch (e) {
    var main = document.getElementById('info-main');
    if (main) main.innerHTML = '<div class="info-loading">Failed to load docs.</div>';
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
    btn.addEventListener('click', function() {
      document.querySelectorAll('.info-nav-btn, .info-comp-btn').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      showDocPage(c.id);
    });
    nav.appendChild(btn);
  });
}

document.querySelectorAll('.info-nav-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.info-nav-btn, .info-comp-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    showDocPage(btn.dataset.doc);
  });
});

function showDocPage(page) {
  var main = document.getElementById('info-main');
  if (!main) return;
  if (page === 'overview' && docsData) {
    var idx = docsData.index;
    var html = '<h2 class="info-title">' + (idx.title || 'PiCar Pro') + '</h2>';
    if (idx.description) html += '<p class="info-subtitle">' + idx.description + '</p>';
    if (idx.features) {
      html += '<div class="info-section"><h3 class="info-section-title">Features</h3><ul class="tips-list">';
      idx.features.forEach(function(f) { html += '<li>' + f + '</li>'; });
      html += '</ul></div>';
    }
    main.innerHTML = html;
    return;
  }
  if (page === 'pinout' && docsData && docsData.pinout) {
    var p = docsData.pinout;
    var html = '<h2 class="info-title">' + (p.title || 'Pinout') + '</h2>';
    html += '<img src="/rpi_pinout.png" style="max-width:100%;border-radius:8px;margin-bottom:12px">';
    if (p.pins) {
      html += '<div class="info-section"><h3 class="info-section-title">Pin Assignments</h3>';
      html += '<table class="pin-table"><thead><tr><th>GPIO</th><th>Name</th><th>Module</th><th>Notes</th></tr></thead><tbody>';
      p.pins.filter(function(pin) { return pin.gpio !== null; }).forEach(function(pin) {
        html += '<tr><td>GPIO' + pin.gpio + '</td><td>' + (pin.name || pin.function || '') + '</td><td>' + (pin.module || '') + '</td><td>' + (pin.notes || '') + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }
    main.innerHTML = html;
    return;
  }
  if (docsData && docsData.components[page]) {
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
    if (d.i2c_address !== undefined) {
      html += '<div class="info-section"><h3 class="info-section-title">I2C Info</h3><div class="i2c-device-grid"><div class="i2c-device"><div class="i2c-device-addr">0x' + d.i2c_address.toString(16).toUpperCase() + '</div><div class="i2c-device-name">' + comp.name + '</div></div></div></div>';
    }
    main.innerHTML = html;
  }
}

buildServoGrid();
wsConnect();
fetchConsoleHistory();
