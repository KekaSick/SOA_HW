"""Реализация сгенерированных методов регистрации, входа и обновления токенов."""
import base64
import hashlib

import bcrypt

from openapi_server.apis.auth_api_base import BaseAuthApi
from openapi_server.models.token_response import TokenResponse
from openapi_server.models.user_response import UserResponse

from app import db
from app.errors import ApiErrorException, response
from app.security import tokens, verify


def password_bytes(password):
    # Фиксированная длина позволяет bcrypt учитывать весь UTF-8 пароль, включая символы за 72-м байтом.
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def password_matches(password, stored):
    if stored.startswith('sha256$'):
        return bcrypt.checkpw(password_bytes(password), stored[7:].encode())
    # Учётные записи из существующей базы и учебной миграции сохраняют доступ.
    return bcrypt.checkpw(password.encode(), stored.encode())


class AuthApi(BaseAuthApi):
    @db.threaded
    def register(self, register_request):
        request = register_request
        with db.connection() as conn:
            if conn.execute('SELECT id FROM users WHERE username = %s', (request.username,)).fetchone():
                raise ApiErrorException(400, 'VALIDATION_ERROR', 'Имя пользователя занято',
                                        {'username': 'Должно быть уникальным'})
            password_hash = 'sha256$' + bcrypt.hashpw(password_bytes(request.password), bcrypt.gensalt()).decode()
            user = conn.execute('INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id, username, role',
                                (request.username, password_hash)).fetchone()
            return response(UserResponse, user, 201)

    @db.threaded
    def login(self, login_request):
        with db.connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE username = %s', (login_request.username,)).fetchone()
            if user is None or not password_matches(login_request.password, user['password_hash']):
                raise ApiErrorException(401, 'TOKEN_INVALID', 'Неверное имя пользователя или пароль')
            return response(TokenResponse, tokens(user))

    @db.threaded
    def refresh(self, refresh_request):
        identity = verify(refresh_request.refresh_token, 'refresh')
        with db.connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE id = %s', (identity['id'],)).fetchone()
            if user is None:
                raise ApiErrorException(401, 'REFRESH_TOKEN_INVALID', 'Пользователь не найден')
            return response(TokenResponse, tokens(user))
