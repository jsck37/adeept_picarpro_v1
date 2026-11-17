/* PiCar Pro — светлая / тёмная тема (v1.6.9).
 * Подключается в <head> ДО отрисовки (CSP запрещает inline-скрипты),
 * поэтому страница сразу открывается в нужной теме, без вспышки.
 * Выбор хранится в localStorage (picarpro_theme: 'light' | 'dark').
 * Пока пользователь не выбирал вручную — следуем системной теме. */
(function (root) {
  'use strict';
  var KEY = 'picarpro_theme';
  var COLORS = { light: '#f4f5fb', dark: '#0e1020' };   // для <meta name="theme-color">

  function stored() {
    try {
      var v = root.localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) { return null; }
  }
  function system() {
    try {
      return root.matchMedia && root.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark' : 'light';
    } catch (e) { return 'light'; }
  }
  function current() { return stored() || system(); }

  function apply(theme) {
    var el = root.document.documentElement;
    el.setAttribute('data-theme', theme);
    el.style.colorScheme = theme;
    var meta = root.document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', COLORS[theme]);
    return theme;
  }
  function set(theme) {
    if (theme !== 'light' && theme !== 'dark') return current();
    try { root.localStorage.setItem(KEY, theme); } catch (e) {}
    return apply(theme);
  }
  function toggle() { return set(current() === 'dark' ? 'light' : 'dark'); }

  root.PicarTheme = { get: current, set: set, toggle: toggle, apply: apply, KEY: KEY };
  apply(current());

  // системная тема меняется — следуем ей, только если выбор не сделан вручную
  try {
    var mq = root.matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () { if (!stored()) apply(system()); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  } catch (e) {}
})(window);
