"""Единый формат ошибок из OpenAPI-контракта."""
from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from openapi_server.models.api_error import ApiError


class ApiErrorException(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.error = ApiError(error_code=code, message=message, details=details)


def response(model, data, status=200):
    value = model.model_validate(data).model_dump(mode='python', by_alias=True)
    return JSONResponse(jsonable_encoder(value, custom_encoder={Decimal: float}), status_code=status)


def error_response(error):
    return response(ApiError, error.error.model_dump(), error.status)
