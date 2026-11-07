"""WebSocket-сервер управления (порт 8888) на библиотеке websockets.

Исправления относительно исходной версии:
* совместимость с websockets >= 10 и >= 14 (хендлер с опциональным path);
* при AUTH_TOKEN первым сообщением ожидается {"cmd":"auth","params":{"token":...}},
  иначе соединение закрывается с ошибкой (в оригинале был admin:123456
  открытым текстом либо полный отказ от аутентификации в APPServer v2);
* исключения обработки команд не роняют соединение и не раскрывают стек.
"""
import asyncio
import itertools
import json
import time

import websockets

from picarpro.logger import logger, log_buffer
from picarpro.commands import process as process_command
from picarpro.web.auth import check_token, token_required
from config import (WEBSOCKET_PORT, TELEMETRY_INTERVAL_S,
                    WS_STATUS_FULL_EVERY_S, WS_STATUS_HIDDEN_INTERVAL_S,
                    WS_SEND_TIMEOUT_S)

AUTH_TIMEOUT_S = 10.0
_conn_counter = itertools.count(1)


def status_delta(meta, status, now):
    """Что отправить клиенту: (данные, is_delta) или (None, False), если
    нечего слать. Полный статус — первый и раз в WS_STATUS_FULL_EVERY_S;
    между ними только изменившиеся поля (hw, servo_limits и т.п. почти
    не меняются, но раньше уходили каждые 2 с)."""
    if meta is None:
        return status, False
    last = meta.get('last')
    if last is None or now - meta.get('full_ts', 0) >= WS_STATUS_FULL_EVERY_S:
        meta['last'], meta['full_ts'] = dict(status), now
        return status, False
    delta = {k: v for k, v in status.items() if last.get(k, _MISSING) != v}
    if not delta:
        return None, False
    last.update(delta)
    return delta, True


_MISSING = object()


def _authorized_now():
    return not token_required()


async def ws_handler(state, ws, path=None):
    # --- аутентификация ----------------------------------------------------
    if not _authorized_now():
        try:
            await ws.send(json.dumps({'type': 'auth_required'}))
            msg = await asyncio.wait_for(ws.recv(), timeout=AUTH_TIMEOUT_S)
            data = json.loads(msg)
            token = (data.get('params') or {}).get('token', '') \
                if isinstance(data, dict) else ''
            if not check_token(token):
                await ws.send(json.dumps(
                    {'type': 'response',
                     'data': {'ok': False, 'error': 'Unauthorized'}}))
                await ws.close()
                return
            await ws.send(json.dumps({'type': 'auth_ok'}))
        except (asyncio.CancelledError, Exception):
            # таймаут, битый JSON, обрыв до аутентификации — всё то же закрытие
            # (CancelledError — это BaseException, поэтому перечислен явно)
            try:
                await ws.close()
            except Exception:
                pass
            return

    state.ws_clients.add(ws)
    meta = {'id': f'ws{next(_conn_counter)}', 'visible': True,
            'last': None, 'full_ts': 0.0, 'sent_ts': 0.0}
    state.ws_meta[ws] = meta
    try:
        first, _ = status_delta(meta, state.get_status(), time.monotonic())
        await ws.send(json.dumps({'type': 'status', 'data': first}))
        recent = log_buffer.get_lines(last_n=100)
        if recent:
            await ws.send(json.dumps({
                'type': 'log_history',
                'lines': [[ts, txt] for ts, txt in recent],
            }))
    except Exception:
        pass

    log_queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_log(ts, text):
        # вызывается из ЛЮБОГО потока (логгер), а asyncio.Queue не потокобезопасна:
        # put_nowait из чужого потока не будил ожидающий get() — доставка
        # держалась только на опросе с таймаутом 0.5 с
        try:
            loop.call_soon_threadsafe(log_queue.put_nowait, (ts, text))
        except Exception:
            pass       # цикл уже закрыт

    log_buffer.subscribe(on_log)
    log_task = asyncio.create_task(_forward_logs(ws, log_queue, state))
    try:
        async for msg in ws:
            try:
                data = json.loads(msg)
                if not isinstance(data, dict):
                    raise ValueError('object expected')
                cmd = data.get('cmd')
                params = data.get('params') or {}
                if cmd == 'hello':          # идентификатор устройства и видимость
                    cid = str(params.get('id') or '')[:32]
                    if cid:
                        meta['id'] = cid
                    meta['visible'] = bool(params.get('visible', True))
                    r = {'ok': True, 'cmd': 'hello', 'id': meta['id'],
                         'driver': state.control.driver}
                elif cmd == 'view':         # вкладка скрыта -> статус реже
                    meta['visible'] = bool(params.get('visible', True))
                    meta['sent_ts'] = 0.0
                    r = {'ok': True, 'cmd': 'view'}
                else:
                    # команды синхронные и бывают долгими (плавное серво, скан
                    # BT/I2C): в потоке, иначе они замораживали весь цикл
                    # событий — телеметрию и остальных клиентов
                    r = await loop.run_in_executor(
                        None, process_command, state, data, meta['id'])
                    if isinstance(r, dict):
                        # клиент сразу узнаёт, кто водитель (без ожидания статуса)
                        r.setdefault('driver', state.control.driver)
                await ws.send(json.dumps({'type': 'response', 'data': r}))
            except json.JSONDecodeError:
                await ws.send(json.dumps(
                    {'type': 'response',
                     'data': {'ok': False, 'error': 'Invalid JSON'}}))
            except Exception as e:
                await ws.send(json.dumps(
                    {'type': 'response',
                     'data': {'ok': False, 'error': str(e)[:200]}}))
    except Exception:
        pass
    finally:
        log_task.cancel()
        log_buffer.unsubscribe(on_log)
        state.ws_clients.discard(ws)
        state.ws_meta.pop(ws, None)
        state.control.release(meta['id'])   # водитель ушёл — управление свободно
        try:
            await loop.run_in_executor(None, state.on_last_client_gone)
        except Exception:
            pass


async def _forward_logs(ws, queue, state):
    """Пересылка логов клиенту: ждём события, а не опрашиваем (раньше —
    пробуждение каждые 0.5 с на каждого клиента). Задача отменяется при
    закрытии соединения."""
    try:
        while True:
            ts, text = await queue.get()
            await ws.send(json.dumps({'type': 'log', 'text': text, 'ts': ts}))
    except Exception:
        pass


async def _send_one(ws, msg):
    """Отправка с таймаутом: медленный клиент не задерживает остальных."""
    try:
        await asyncio.wait_for(ws.send(msg), timeout=WS_SEND_TIMEOUT_S)
        return None
    except Exception:
        return ws


async def status_broadcast(state):
    loop = asyncio.get_running_loop()
    while state.running:
        if state.ws_clients:
            try:
                status = await loop.run_in_executor(None, state.get_status)
                now = time.monotonic()
                jobs = []
                for ws in list(state.ws_clients):
                    meta = state.ws_meta.get(ws)
                    if (meta is not None and not meta['visible']
                            and now - meta['sent_ts'] < WS_STATUS_HIDDEN_INTERVAL_S):
                        continue                 # вкладка скрыта — не тревожим
                    data, _ = status_delta(meta, status, now)
                    if data is None:
                        continue
                    if meta is not None:
                        meta['sent_ts'] = now
                    jobs.append(_send_one(
                        ws, json.dumps({'type': 'status', 'data': data,
                                        'delta': data is not status})))
                gone = {w for w in await asyncio.gather(*jobs) if w is not None}
                state.ws_clients -= gone
            except Exception:
                pass
        # TELEMETRY_INTERVAL_S (2 c): get_status() читает I2C/GPIO —
        # 1 Гц не давал ничего, кроме лишней нагрузки (v1.2.0)
        await asyncio.sleep(TELEMETRY_INTERVAL_S)


async def _ws_main(state):
    async with websockets.serve(
        lambda ws, path=None: ws_handler(state, ws, path),
        '0.0.0.0', WEBSOCKET_PORT,
        # v1.2.0: экономия CPU — сжатие на мелких JSON-сообщениях дороже,
        # чем выгодно; ping реже; очередь короче
        compression=None,
        ping_interval=30, ping_timeout=20,
        max_queue=4,
    ):
        logger.info(f'[Web] WebSocket on :{WEBSOCKET_PORT}')
        await status_broadcast(state)


def run_websocket_server(state):
    try:
        asyncio.run(_ws_main(state))
    except KeyboardInterrupt:
        pass
