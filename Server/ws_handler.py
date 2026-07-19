import asyncio, json
import websockets
from Server.utils.log_buffer import log_buffer
from Server.commands import process_command


async def ws_handler(state, ws, path=None):
    state.ws_clients.add(ws)
    try:
        await ws.send(json.dumps({'type': 'status', 'data': state.get_status()}))
        recent = log_buffer.get_lines(last_n=100)
        if recent:
            await ws.send(json.dumps({
                'type': 'log_history',
                'lines': [[ts, txt] for ts, txt in recent],
            }))
    except Exception:
        pass

    log_queue = asyncio.Queue()

    def on_log(text):
        try:
            log_queue.put_nowait(text)
        except Exception:
            pass

    log_buffer.subscribe(on_log)

    try:
        async def forward_logs():
            try:
                while state.running:
                    try:
                        text = await asyncio.wait_for(log_queue.get(), timeout=0.5)
                        await ws.send(json.dumps({'type': 'log', 'text': text}))
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
            except Exception:
                pass

        log_task = asyncio.create_task(forward_logs())

        async for msg in ws:
            try:
                r = process_command(state, json.loads(msg))
                await ws.send(json.dumps({'type': 'response', 'data': r}))
            except json.JSONDecodeError:
                await ws.send(json.dumps({
                    'type': 'response',
                    'data': {'ok': False, 'error': 'Invalid JSON'},
                }))
            except Exception as e:
                await ws.send(json.dumps({
                    'type': 'response',
                    'data': {'ok': False, 'error': str(e)},
                }))
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception:
        pass
    finally:
        log_task.cancel()
        log_buffer.unsubscribe(on_log)
        state.ws_clients.discard(ws)


async def status_broadcast(state):
    while state.running:
        if state.ws_clients:
            try:
                msg = json.dumps({'type': 'status', 'data': state.get_status()})
                gone = set()
                for ws in state.ws_clients:
                    try:
                        await ws.send(msg)
                    except Exception:
                        gone.add(ws)
                state.ws_clients -= gone
            except Exception:
                pass
        await asyncio.sleep(1.5)
