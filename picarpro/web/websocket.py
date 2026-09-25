"""WebSocket-сервер управления (порт 8888) на библиотеке websockets.

Исправления относительно исходной версии:
* совместимость с websockets >= 10 и >= 14 (хендлер с опциональным path);
* при AUTH_TOKEN первым сообщением ожидается {"cmd":"auth","params":{"token":...}},
  иначе соединение закрывается с ошибкой (в оригинале был admin:123456
  открытым текстом либо полный отказ от аутентификации в APPServer v2);
* исключения обработки команд не роняют соединение и не раскрывают стек.
"""
import asyncio
import json

import websockets

from picarpro.logger import logger, log_buffer
from picarpro.commands import process as process_command
from picarpro.web.auth import check_token, token_required
from config import WEBSOCKET_PORT

AUTH_TIMEOUT_S = 10.0


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
        except (asyncio.TimeoutError, asyncio.CancelledError,
                json.JSONDecodeError, Exception):
            try:
                await ws.close()
            except Exception:
                pass
            return

    state.ws_clients.add(ws)
    try:
        await ws.send(json.dumps(
            {'type': 'status', 'data': state.get_status()}))
        recent = log_buffer.get_lines(last_n=100)
        if recent:
            await ws.send(json.dumps({
                'type': 'log_history',
                'lines': [[ts, txt] for ts, txt in recent],
            }))
    except Exception:
        pass

    log_queue = asyncio.Queue()

    def on_log(ts, text):
        try:
            log_queue.put_nowait((ts, text))
        except Exception:
            pass

    log_buffer.subscribe(on_log)
    log_task = asyncio.create_task(_forward_logs(ws, log_queue, state))
    try:
        async for msg in ws:
            try:
                data = json.loads(msg)
                if not isinstance(data, dict):
                    raise ValueError('object expected')
                r = process_command(state, data)
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


async def _forward_logs(ws, queue, state):
    try:
        while state.running:
            try:
                ts, text = await asyncio.wait_for(queue.get(), timeout=0.5)
                await ws.send(json.dumps({'type': 'log', 'text': text,
                                          'ts': ts}))
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
    except Exception:
        pass


async def status_broadcast(state):
    while state.running:
        if state.ws_clients:
            try:
                msg = json.dumps({'type': 'status',
                                  'data': state.get_status()})
                gone = set()
                for ws in list(state.ws_clients):
                    try:
                        await ws.send(msg)
                    except Exception:
                        gone.add(ws)
                state.ws_clients -= gone
            except Exception:
                pass
        await asyncio.sleep(1.0)


async def _ws_main(state):
    async with websockets.serve(
        lambda ws, path=None: ws_handler(state, ws, path),
        '0.0.0.0', WEBSOCKET_PORT,
    ):
        logger.info(f'[Web] WebSocket on :{WEBSOCKET_PORT}')
        await status_broadcast(state)


def run_websocket_server(state):
    try:
        asyncio.run(_ws_main(state))
    except KeyboardInterrupt:
        pass
