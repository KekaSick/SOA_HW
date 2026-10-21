"""SQL-запросы и соединение с PostgreSQL. Контекст соединения задаёт транзакцию."""
from functools import wraps
import os

import psycopg
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from app.errors import ApiErrorException


def connection():
    return psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row)


def threaded(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        return await run_in_threadpool(function, *args, **kwargs)
    return wrapped


def product(conn, product_id, lock=False):
    row = conn.execute('SELECT * FROM products WHERE id = %s' + (' FOR UPDATE' if lock else ''),
                       (product_id,)).fetchone()
    if row is None:
        raise ApiErrorException(404, 'PRODUCT_NOT_FOUND', 'Товар не найден')
    return row


def order(conn, order_id, lock=False):
    row = conn.execute('SELECT * FROM orders WHERE id = %s' + (' FOR UPDATE' if lock else ''),
                       (order_id,)).fetchone()
    if row is None:
        raise ApiErrorException(404, 'ORDER_NOT_FOUND', 'Заказ не найден')
    row['items'] = conn.execute('SELECT * FROM order_items WHERE order_id = %s ORDER BY id',
                                (order_id,)).fetchall()
    for line in row['items']:
        del line['order_id']
    return row


def promo(conn, promo_id):
    return conn.execute('SELECT * FROM promo_codes WHERE id = %s FOR UPDATE', (promo_id,)).fetchone()


def lock_users(conn, *ids):
    for user_id in sorted(set(ids)):
        conn.execute('SELECT id FROM users WHERE id = %s FOR UPDATE', (user_id,)).fetchone()


def lock_products(conn, ids):
    for product_id in sorted(set(ids)):
        conn.execute('SELECT id FROM products WHERE id = %s FOR UPDATE', (product_id,)).fetchone()
