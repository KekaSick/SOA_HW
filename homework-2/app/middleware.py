"""Авторизация и одна JSON-запись лога на каждый API-запрос."""
from datetime import datetime, timezone
import json
import posixpath
from time import perf_counter
import uuid

from app.errors import ApiErrorException, error_response
from app.security import actor, require, verify


class ApiMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = posixpath.normpath(scope['path'])
        if path.startswith(('/swagger-ui/', '/swagger-assets/', '/openapi/')):
            return await self.app(scope, receive, send)
        start = perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        request_id = str(uuid.uuid4())
        method, status, identity = scope['method'], 500, None
        response_started = False
        body = b''
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body += message.get('body', b'')
            if not message.get('more_body'):
                break
        consumed = False

        async def cached_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        async def logged_send(message):
            nonlocal status, response_started
            if message['type'] == 'http.response.start':
                response_started = True
                status = message['status']
                message.setdefault('headers', []).append((b'x-request-id', request_id.encode()))
            await send(message)

        context = actor.set(None)
        try:
            if path not in {'/auth/register', '/auth/login', '/auth/refresh'}:
                headers = dict(scope['headers'])
                authorization = headers.get(b'authorization', b'').decode()
                if not authorization.startswith('Bearer '):
                    raise ApiErrorException(401, 'TOKEN_INVALID', 'Требуется access token')
                identity = verify(authorization[7:])
                actor.set(identity)
                if path.startswith('/orders'):
                    require('USER')
                if (path.startswith('/products') and method in {'POST', 'PUT', 'DELETE'}) or path == '/promo-codes':
                    require('SELLER')
            await self.app(scope, cached_receive, logged_send)
        except ApiErrorException as error:
            await error_response(error)(scope, cached_receive, logged_send)
        except Exception:
            if response_started:
                raise
            error = ApiErrorException(500, 'INTERNAL_ERROR', 'Внутренняя ошибка сервера')
            await error_response(error)(scope, cached_receive, logged_send)
        finally:
            entry = {'request_id': request_id, 'method': method, 'endpoint': path, 'status_code': status,
                     'duration_ms': (perf_counter() - start) * 1000,
                     'user_id': identity['id'] if identity else None, 'timestamp': timestamp}
            if method in {'POST', 'PUT', 'DELETE'}:
                try:
                    entry['request_body'] = mask(json.loads(body)) if body else None
                except (ValueError, UnicodeDecodeError):
                    entry['request_body'] = 'Некорректный JSON'
            print(json.dumps(entry, ensure_ascii=False), flush=True)
            actor.reset(context)


def mask(value):
    if isinstance(value, dict):
        return {key: '***' if key.lower() in {'password', 'access_token', 'refresh_token'} else mask(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [mask(item) for item in value]
    return value
