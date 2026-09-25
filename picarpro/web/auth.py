"""Аутентификация и заголовки безопасности для веб-интерфейса.

Политика:
* AUTH_TOKEN задан  -> все /api/*, /cmd/* и WebSocket требуют токен
  (заголовок X-Auth-Token, параметр ?token= или WS-команда {"cmd":"auth"}).
  Статические файлы UI и /video_feed с токеном-параметром разрешены, чтобы
  <img src="/video_feed?token=..."> работал из браузера.
* AUTH_TOKEN пуст   -> авторизация отключена (доверенная LAN-сеть по
  умолчанию), токен не требуется.
* Сравнение токенов — через hmac.compare_digest (защита от timing-атак).
"""
import hmac
import secrets

from flask import request

from config import AUTH_TOKEN

TOKEN_HEADER = 'X-Auth-Token'
TOKEN_PARAM = 'token'

# Пути, всегда открытые без токена (статика UI)
PUBLIC_PREFIXES = (
    '/', '/app.js', '/style.css', '/rpi_pinout.png', '/favicon.ico',
    '/api/auth/check',
)


def token_required():
    return bool(AUTH_TOKEN)


def check_token(provided):
    if not AUTH_TOKEN:
        return True
    if not provided:
        return False
    return hmac.compare_digest(str(provided), AUTH_TOKEN)


def extract_request_token():
    """Достать токен из заголовка или параметра запроса."""
    return (request.headers.get(TOKEN_HEADER)
            or request.args.get(TOKEN_PARAM)
            or '')


def request_authorized():
    return check_token(extract_request_token())


def is_public_path(path):
    if path in PUBLIC_PREFIXES:
        return True
    return path.startswith('/docs/')  # справочная информация — публичная


def generate_secret_key(persist_path=None):
    """SECRET_KEY из окружения или сгенерированный и сохранённый на диск."""
    import os
    env = os.environ.get('PICARPRO_SECRET_KEY', '')
    if env:
        return env
    if persist_path:
        try:
            if os.path.isfile(persist_path):
                with open(persist_path) as f:
                    key = f.read().strip()
                    if key:
                        return key
            key = secrets.token_hex(32)
            os.makedirs(os.path.dirname(persist_path), exist_ok=True)
            with open(persist_path, 'w') as f:
                f.write(key)
            try:
                os.chmod(persist_path, 0o600)
            except Exception:
                pass
            return key
        except Exception:
            pass
    return secrets.token_hex(32)


SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    # UI не использует inline-скрипты: app.js подключается внешним файлом
    "Content-Security-Policy":
        "default-src 'self'; img-src 'self' data:; "
        "media-src 'self'; connect-src 'self' ws: wss:; "
        "style-src 'self' 'unsafe-inline'",
}
