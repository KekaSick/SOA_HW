"""CRUD товаров через сгенерированный интерфейс и модели."""
from starlette.responses import Response

from openapi_server.apis.products_api_base import BaseProductsApi
from openapi_server.models.product_page import ProductPage
from openapi_server.models.product_response import ProductResponse

from app import db
from app.errors import ApiErrorException, response
from app.security import require


def owned(product):
    user = require('SELLER')
    if user['role'] != 'ADMIN' and user['id'] != product['seller_id']:
        raise ApiErrorException(403, 'ACCESS_DENIED', 'Товар принадлежит другому продавцу')


class ProductsApi(BaseProductsApi):
    @db.threaded
    def create_product(self, product_create):
        user = require('SELLER')
        p = product_create
        with db.connection() as conn:
            row = conn.execute('''INSERT INTO products (name, description, price, stock, category, status, seller_id)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *''',
                               (p.name, p.description, p.price, p.stock, p.category, p.status.value, user['id'])).fetchone()
            return response(ProductResponse, row, 201)

    @db.threaded
    def get_product(self, id):
        with db.connection() as conn:
            return response(ProductResponse, db.product(conn, id))

    @db.threaded
    def list_products(self, page=0, size=20, status=None, category=None):
        where, params = [], []
        if status is not None:
            where.append('status = %s')
            params.append(status.value)
        if category is not None:
            where.append('category = %s')
            params.append(category)
        condition = ' WHERE ' + ' AND '.join(where) if where else ''
        with db.connection() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            total = conn.execute('SELECT count(*) AS total FROM products' + condition, params).fetchone()['total']
            items = conn.execute('SELECT * FROM products' + condition + ' ORDER BY id LIMIT %s OFFSET %s',
                                 [*params, size, page * size]).fetchall()
            return response(ProductPage, {'items': items, 'totalElements': total, 'page': page, 'size': size})

    @db.threaded
    def update_product(self, id, product_update):
        p = product_update
        with db.connection() as conn:
            owned(db.product(conn, id, lock=True))
            row = conn.execute('''UPDATE products SET name=%s, description=%s, price=%s, stock=%s, category=%s, status=%s
                                  WHERE id=%s RETURNING *''',
                               (p.name, p.description, p.price, p.stock, p.category, p.status.value, id)).fetchone()
            return response(ProductResponse, row)

    @db.threaded
    def delete_product(self, id):
        with db.connection() as conn:
            owned(db.product(conn, id, lock=True))
            conn.execute("UPDATE products SET status = 'ARCHIVED' WHERE id = %s", (id,))
            return Response(status_code=204)
