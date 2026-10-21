"""Создание промокодов продавцом или администратором."""
from openapi_server.apis.promo_codes_api_base import BasePromoCodesApi
from openapi_server.models.promo_code_response import PromoCodeResponse

from app import db
from app.errors import ApiErrorException, response
from app.security import require


class PromoCodesApi(BasePromoCodesApi):
    @db.threaded
    def create_promo_code(self, promo_code_create):
        require('SELLER')
        p = promo_code_create
        if p.valid_until < p.valid_from:
            raise ApiErrorException(400, 'VALIDATION_ERROR', 'Некорректный период действия',
                                    {'valid_until': 'Раньше valid_from'})
        with db.connection() as conn:
            row = conn.execute('''INSERT INTO promo_codes
                                  (code, discount_type, discount_value, min_order_amount, max_uses, valid_from, valid_until, active)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *''',
                               (p.code, p.discount_type.value, p.discount_value, p.min_order_amount,
                                p.max_uses, p.valid_from, p.valid_until, p.active)).fetchone()
            return response(PromoCodeResponse, row, 201)
