"""Проверка JWT и прав текущего пользователя."""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import os
import uuid

import jwt

from app.errors import ApiErrorException

actor = ContextVar('actor', default=None)
SECRET = os.environ.get('JWT_SECRET', 'homework-local-secret-at-least-32-characters')


def tokens(user):
    now = datetime.now(timezone.utc)

    def encode(kind, seconds):
        return jwt.encode({'iss': 'marketplace', 'sub': str(user['id']), 'role': user['role'],
                           'type': kind, 'iat': now, 'exp': now + timedelta(seconds=seconds),
                           'jti': str(uuid.uuid4())}, SECRET, algorithm='HS256')

    return {'access_token': encode('access', 900), 'refresh_token': encode('refresh', 7 * 86400),
            'token_type': 'Bearer', 'expires_in': 900}


def verify(token, kind='access'):
    invalid = 'REFRESH_TOKEN_INVALID' if kind == 'refresh' else 'TOKEN_INVALID'
    try:
        payload = jwt.decode(token, SECRET, algorithms=['HS256'], issuer='marketplace',
                             options={'require': ['exp', 'iat', 'sub', 'role', 'type']})
        user_id = int(payload['sub'])
        if user_id < 1 or payload['role'] not in {'USER', 'SELLER', 'ADMIN'} or payload['type'] != kind:
            raise ValueError()
        return {'id': user_id, 'role': payload['role']}
    except jwt.ExpiredSignatureError:
        code = invalid if kind == 'refresh' else 'TOKEN_EXPIRED'
        raise ApiErrorException(401, code, 'Срок действия токена истёк')
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise ApiErrorException(401, invalid, 'Недопустимый токен')


def require(role):
    user = actor.get()
    if user['role'] not in {role, 'ADMIN'}:
        raise ApiErrorException(403, 'ACCESS_DENIED', 'Недостаточно прав')
    return user


def own_order(order):
    user = require('USER')
    if user['role'] != 'ADMIN' and user['id'] != order['user_id']:
        raise ApiErrorException(403, 'ORDER_OWNERSHIP_VIOLATION', 'Заказ принадлежит другому пользователю')
