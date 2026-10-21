"""Сборка приложения из сгенерированных маршрутов и Python-реализаций."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from psycopg import DataError, IntegrityError
from starlette.exceptions import HTTPException
from swagger_ui_bundle import swagger_ui_path

# Импорт регистрирует реализации в сгенерированных базовых классах.
from app import auth, orders, products, promo_codes
from app.errors import ApiErrorException, error_response
from app.middleware import ApiMiddleware
from app.security import actor
from openapi_server.apis.auth_api import router as auth_router
from openapi_server.apis.orders_api import router as orders_router
from openapi_server.apis.products_api import router as products_router
from openapi_server.apis.promo_codes_api import router as promos_router
from openapi_server.security_api import get_token_bearerAuth

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(ApiMiddleware)
app.dependency_overrides[get_token_bearerAuth] = lambda: actor.get()
for router in [auth_router, products_router, orders_router, promos_router]:
    app.include_router(router)
app.mount('/openapi', StaticFiles(directory=ROOT / 'openapi'), name='openapi')
app.mount('/swagger-ui', StaticFiles(directory=ROOT / 'static/swagger-ui', html=True), name='swagger-ui')
app.mount('/swagger-assets', StaticFiles(directory=swagger_ui_path), name='swagger-assets')


@app.exception_handler(ApiErrorException)
async def business_error(request: Request, error: ApiErrorException):
    return error_response(error)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    details = {}
    for violation in error.errors():
        field = ''
        for part in violation['loc']:
            if part in {'body', 'query', 'path'}:
                continue
            field += f'[{part}]' if isinstance(part, int) else ('.' if field else '') + str(part)
        details[field or 'body'] = violation['msg']
    return error_response(ApiErrorException(400, 'VALIDATION_ERROR', 'Ошибка валидации входных данных', details))


@app.exception_handler(DataError)
@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, error: IntegrityError | DataError):
    field = {'users_username_key': 'username', 'promo_codes_code_key': 'code'}.get(error.diag.constraint_name, 'body')
    return error_response(ApiErrorException(400, 'VALIDATION_ERROR', 'Нарушено ограничение данных',
                                            {field: 'Проверьте уникальность и допустимый диапазон'}))


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    return error_response(ApiErrorException(400, 'VALIDATION_ERROR', 'Некорректный запрос', {'request': error.detail}))


@app.exception_handler(Exception)
async def internal_error(request: Request, error: Exception):
    return error_response(ApiErrorException(500, 'INTERNAL_ERROR', 'Внутренняя ошибка сервера'))
