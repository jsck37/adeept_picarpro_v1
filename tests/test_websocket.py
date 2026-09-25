"""Тесты WebSocket-обработчика: аутентификация, команды, ошибки."""
import asyncio
import json

import pytest


class FakeWS:
    """Минимальный фейк websocket-соединения (sync-очередь вместо async recv)."""

    def __init__(self, incoming=None):
        self.sent = []
        self.incoming = list(incoming or [])
        self.closed = False

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    async def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise asyncio.CancelledError()

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise StopAsyncIteration


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def state(clean_state, monkeypatch):
    # не ждём real-time циклов статуса
    return clean_state


class TestProtocol:
    def test_command_roundtrip(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        ws = FakeWS(incoming=[json.dumps({'cmd': 'speed',
                                          'params': {'value': 42}})])
        run(ws_mod.ws_handler(state, ws))
        types = [m['type'] for m in ws.sent]
        assert 'status' in types
        responses = [m['data'] for m in ws.sent if m['type'] == 'response']
        assert responses and responses[0]['ok'] is True
        assert state.speed == 42

    def test_invalid_json(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        ws = FakeWS(incoming=['not json at all'])
        run(ws_mod.ws_handler(state, ws))
        responses = [m['data'] for m in ws.sent if m['type'] == 'response']
        assert responses[-1]['ok'] is False
        assert 'JSON' in responses[-1]['error']

    def test_non_dict_json(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        ws = FakeWS(incoming=[json.dumps([1, 2, 3])])
        run(ws_mod.ws_handler(state, ws))
        responses = [m['data'] for m in ws.sent if m['type'] == 'response']
        assert responses[-1]['ok'] is False

    def test_unknown_command_error(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        ws = FakeWS(incoming=[json.dumps({'cmd': 'explode'})])
        run(ws_mod.ws_handler(state, ws))
        responses = [m['data'] for m in ws.sent if m['type'] == 'response']
        assert responses[-1]['ok'] is False
        assert ws.closed is False  # соединение не рвём


class TestAuth:
    def test_auth_required_first(self, state, monkeypatch):
        """С токеном: без авторизации команды не выполняются."""
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: True)
        monkeypatch.setattr(ws_mod, 'check_token',
                            lambda t: t == 'good-token')
        ws = FakeWS(incoming=[json.dumps({'cmd': 'speed',
                                          'params': {'value': 10}})])
        run(ws_mod.ws_handler(state, ws))
        assert ws.sent and ws.sent[0]['type'] == 'auth_required'
        assert ws.closed is True
        assert state.speed != 10

    def test_auth_ok_then_commands(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: True)
        monkeypatch.setattr(ws_mod, 'check_token',
                            lambda t: t == 'good-token')
        ws = FakeWS(incoming=[
            json.dumps({'cmd': 'auth', 'params': {'token': 'good-token'}}),
            json.dumps({'cmd': 'speed', 'params': {'value': 33}}),
        ])
        run(ws_mod.ws_handler(state, ws))
        types = [m['type'] for m in ws.sent]
        assert types[0] == 'auth_required'
        assert types[1] == 'auth_ok'
        assert state.speed == 33

    def test_auth_timeout(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: True)
        monkeypatch.setattr(ws_mod, 'AUTH_TIMEOUT_S', 0.05)
        ws = FakeWS()  # recv сразу CancelledError
        run(ws_mod.ws_handler(state, ws))
        assert ws.closed is True


class TestClientRegistry:
    def test_client_added_and_removed(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        ws = FakeWS()
        run(ws_mod.ws_handler(state, ws))
        assert ws not in state.ws_clients


class TestStatusBroadcast:
    def test_broadcast_sends_to_clients(self, state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        sent = []

        class ClientWS:
            async def send(self, msg):
                sent.append(json.loads(msg))

        cw = ClientWS()
        state.ws_clients.add(cw)

        async def run_once():
            task = asyncio.create_task(ws_mod.status_broadcast(state))
            await asyncio.sleep(1.3)
            state.running = False
            await asyncio.sleep(0.1)
            task.cancel()

        try:
            asyncio.new_event_loop().run_until_complete(run_once())
        finally:
            state.ws_clients.discard(cw)
        assert any(m['type'] == 'status' for m in sent)
