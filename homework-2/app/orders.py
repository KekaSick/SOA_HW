"""Создание, обновление и отмена заказа в транзакциях PostgreSQL."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import os

from openapi_server.apis.orders_api_base import BaseOrdersApi
from openapi_server.models.order_response import OrderResponse

from app import db
from app.errors import ApiErrorException, response
from app.security import actor, own_order, require

INTERVAL_MINUTES = int(os.environ.get('ORDER_INTERVAL_MINUTES', '1'))
if INTERVAL_MINUTES < 0:
    raise ValueError('ORDER_INTERVAL_MINUTES >= 0')
CENT = Decimal('0.01')
ZERO = Decimal('0.00')


def rate_limit(conn, user_id, kind):
    last = conn.execute('''SELECT created_at FROM user_operations WHERE user_id=%s AND operation_type=%s
                           ORDER BY created_at DESC, id DESC LIMIT 1''', (user_id, kind)).fetchone()
    if last and last['created_at'] + timedelta(minutes=INTERVAL_MINUTES) > datetime.now(timezone.utc):
        raise ApiErrorException(429, 'ORDER_LIMIT_EXCEEDED', 'Превышен лимит частоты операций')


def operation(conn, user_id, kind):
    conn.execute('INSERT INTO user_operations (user_id, operation_type) VALUES (%s, %s)', (user_id, kind))


def locked_order(conn, order_id):
    initial = db.order(conn, order_id)
    own_order(initial)
    db.lock_users(conn, actor.get()['id'], initial['user_id'])
    order = db.order(conn, order_id, lock=True)
    own_order(order)
    return order


def reserve(conn, items):
    products, quantities = {}, Counter()
    # Проверка всего каталога предшествует проверке остатков.
    for item in items:
        product = db.product(conn, item.product_id)
        if product['status'] != 'ACTIVE':
            raise ApiErrorException(409, 'PRODUCT_INACTIVE', 'Товар неактивен')
        products[item.product_id] = product
        quantities[item.product_id] += item.quantity
    missing = [{'product_id': product_id, 'requested': quantity, 'available': products[product_id]['stock']}
               for product_id, quantity in quantities.items() if products[product_id]['stock'] < quantity]
    if missing:
        raise ApiErrorException(409, 'INSUFFICIENT_STOCK', 'Недостаточно товара на складе', {'items': missing})
    for product_id, quantity in quantities.items():
        conn.execute('UPDATE products SET stock=stock-%s WHERE id=%s', (quantity, product_id))
    return [{'product_id': item.product_id, 'quantity': item.quantity,
             'price_at_order': products[item.product_id]['price']} for item in items]


def release(conn, order):
    for line in order['items']:
        conn.execute('UPDATE products SET stock=stock+%s WHERE id=%s', (line['quantity'], line['product_id']))


def subtotal(lines):
    return sum((line['price_at_order'] * line['quantity'] for line in lines), ZERO)


def validate_promo(promo, already_used=False):
    now = datetime.now(timezone.utc)
    uses = promo['current_uses'] - int(already_used)
    if (not promo['active'] or uses >= promo['max_uses'] or
            now < promo['valid_from'] or now > promo['valid_until']):
        raise ApiErrorException(422, 'PROMO_CODE_INVALID', 'Промокод неактивен, истёк или исчерпан')


def discount(promo, total):
    if promo['discount_type'] == 'FIXED_AMOUNT':
        return min(promo['discount_value'], total)
    # По согласованной трактовке задания процент свыше 70 даёт полную стоимость.
    if promo['discount_value'] > 70:
        return ZERO
    return (total * promo['discount_value'] / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def save_items(conn, order_id, lines):
    for line in lines:
        conn.execute('''INSERT INTO order_items (order_id, product_id, quantity, price_at_order)
                        VALUES (%s, %s, %s, %s)''',
                     (order_id, line['product_id'], line['quantity'], line['price_at_order']))


class OrdersApi(BaseOrdersApi):
    @db.threaded
    def create_order(self, order_create):
        user = require('USER')
        request = order_create
        with db.connection() as conn:
            db.lock_users(conn, user['id'])
            rate_limit(conn, user['id'], 'CREATE_ORDER')
            active = conn.execute("SELECT id FROM orders WHERE user_id=%s AND status IN ('CREATED','PAYMENT_PENDING') LIMIT 1",
                                  (user['id'],)).fetchone()
            if active:
                raise ApiErrorException(409, 'ORDER_HAS_ACTIVE', 'У пользователя уже есть активный заказ')
            db.lock_products(conn, [item.product_id for item in request.items])
            lines = reserve(conn, request.items)
            total = subtotal(lines)
            promo_id, amount = None, ZERO
            if request.promo_code is not None:
                promo = conn.execute('SELECT * FROM promo_codes WHERE code=%s FOR UPDATE', (request.promo_code,)).fetchone()
                if promo is None:
                    raise ApiErrorException(422, 'PROMO_CODE_INVALID', 'Промокод не найден')
                validate_promo(promo)
                if total < promo['min_order_amount']:
                    raise ApiErrorException(422, 'PROMO_CODE_MIN_AMOUNT', 'Сумма заказа ниже минимальной для промокода')
                promo_id, amount = promo['id'], discount(promo, total)
                conn.execute('UPDATE promo_codes SET current_uses=current_uses+1 WHERE id=%s', (promo_id,))
            order_id = conn.execute('''INSERT INTO orders (user_id, status, promo_code_id, total_amount, discount_amount)
                                       VALUES (%s, 'CREATED', %s, %s, %s) RETURNING id''',
                                    (user['id'], promo_id, total - amount, amount)).fetchone()['id']
            save_items(conn, order_id, lines)
            operation(conn, user['id'], 'CREATE_ORDER')
            return response(OrderResponse, db.order(conn, order_id), 201)

    @db.threaded
    def get_order(self, id):
        with db.connection() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            order = db.order(conn, id)
            own_order(order)
            return response(OrderResponse, order)

    @db.threaded
    def update_order(self, id, order_update):
        with db.connection() as conn:
            order = locked_order(conn, id)
            if order['status'] != 'CREATED':
                raise ApiErrorException(409, 'INVALID_STATE_TRANSITION', 'Обновление разрешено только в CREATED')
            rate_limit(conn, actor.get()['id'], 'UPDATE_ORDER')
            db.lock_products(conn, [i['product_id'] for i in order['items']] + [i.product_id for i in order_update.items])
            release(conn, order)
            lines = reserve(conn, order_update.items)
            total, amount = subtotal(lines), ZERO
            promo_id = order['promo_code_id']
            if promo_id is not None:
                promo = db.promo(conn, promo_id)
                validate_promo(promo, already_used=True)
                if total < promo['min_order_amount']:
                    conn.execute('UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s', (promo_id,))
                    promo_id = None
                else:
                    amount = discount(promo, total)
            conn.execute('UPDATE orders SET promo_code_id=%s, total_amount=%s, discount_amount=%s WHERE id=%s',
                         (promo_id, total - amount, amount, id))
            conn.execute('DELETE FROM order_items WHERE order_id=%s', (id,))
            save_items(conn, id, lines)
            operation(conn, actor.get()['id'], 'UPDATE_ORDER')
            return response(OrderResponse, db.order(conn, id))

    @db.threaded
    def cancel_order(self, id):
        with db.connection() as conn:
            order = locked_order(conn, id)
            if order['status'] not in {'CREATED', 'PAYMENT_PENDING'}:
                raise ApiErrorException(409, 'INVALID_STATE_TRANSITION', 'Отмена разрешена только в CREATED или PAYMENT_PENDING')
            db.lock_products(conn, [i['product_id'] for i in order['items']])
            release(conn, order)
            if order['promo_code_id'] is not None:
                db.promo(conn, order['promo_code_id'])
                conn.execute('UPDATE promo_codes SET current_uses=current_uses-1 WHERE id=%s', (order['promo_code_id'],))
            conn.execute("UPDATE orders SET status='CANCELED' WHERE id=%s", (id,))
            return response(OrderResponse, db.order(conn, id))
