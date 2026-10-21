"""Сквозные проверки API и транзакций на PostgreSQL из compose.yaml."""
import base64
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import unittest
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get('BASE_URL', 'http://localhost:8082')
ROOT = Path(__file__).resolve().parents[1]


def sql(query):
    return subprocess.check_output(
        ['docker', 'compose', 'exec', '-T', 'db', 'psql', '-U', 'marketplace',
         '-d', 'marketplace', '-At', '-c', query], cwd=ROOT, text=True).strip()


def call(method, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(BASE + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
    try:
        response = urllib.request.urlopen(request)
    except urllib.error.HTTPError as error:
        response = error
    data = response.read()
    try:
        parsed = json.loads(data) if data else None
    except json.JSONDecodeError:
        parsed = data.decode()
    return response.status, parsed, response.headers


class MarketplaceE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # На ДЗ №1 здесь 404: защиты CRUD ещё нет.
        status, error, headers = call('GET', '/products')
        assert status == 401, f'GET /products без токена: ожидался 401, получен {status}'
        uuid.UUID(headers['X-Request-Id'])
        assert error['error_code'] == 'TOKEN_INVALID'
        cls.seller = cls.login('seller')
        cls.admin = cls.login('admin')
        cls.other_seller = cls.login('seller2')

    @classmethod
    def login(cls, username):
        status, data, _ = call('POST', '/auth/login', {'username': username, 'password': 'demo-password'})
        assert status == 200, data
        return data['access_token']

    def request(self, method, path, body=None, token=None, status=200, code=None):
        actual, data, headers = call(method, path, body, token)
        self.assertEqual(status, actual, data)
        uuid.UUID(headers['X-Request-Id'])
        if code:
            self.assertEqual(code, data['error_code'])
        return data

    def user(self):
        name = 'u' + uuid.uuid4().hex
        data = self.request('POST', '/auth/register', {'username': name, 'password': 'test-password'}, status=201)
        return data['id'], self.request('POST', '/auth/login', {'username': name, 'password': 'test-password'})['access_token']

    def product(self, **changes):
        body = dict(name='Товар', description=None, price=100, stock=20, category='test-' + uuid.uuid4().hex, status='ACTIVE')
        body.update(changes)
        return self.request('POST', '/products', body, self.seller, 201)

    def age(self, user_id):
        sql(f"UPDATE user_operations SET created_at = now() - interval '2 hours' WHERE user_id = {user_id}")

    def order(self, token, product, quantity=2, **extra):
        return self.request('POST', '/orders', dict(items=[dict(product_id=product['id'], quantity=quantity)], **extra), token, 201)

    def promo(self, **changes):
        now = datetime.now(timezone.utc)
        body = dict(code=uuid.uuid4().hex[:12].upper(), discount_type='PERCENTAGE', discount_value=20,
                    min_order_amount=100, max_uses=10, valid_from=(now-timedelta(days=1)).isoformat(),
                    valid_until=(now+timedelta(days=1)).isoformat(), active=True)
        body.update(changes)
        return self.request('POST', '/promo-codes', body, self.seller, 201)

    def test_products_crud_filters_roles_and_validation(self):
        _, user = self.user()
        p = self.product()
        self.assertEqual(100, p['price'])
        self.assertIn('created_at', p)
        page = self.request('GET', '/products?category=' + p['category'], token=user)
        self.assertEqual((1, 0, 20), (page['totalElements'], page['page'], page['size']))
        self.assertEqual(p['id'], page['items'][0]['id'])
        self.assertEqual([], self.request('GET', '/products?category=' + p['category'] + '&page=1&size=1', token=user)['items'])
        self.assertEqual(0, self.request('GET', '/products?category=' + p['category'] + '&status=INACTIVE', token=user)['totalElements'])
        self.request('GET', '/products?page=-1', token=user, status=400, code='VALIDATION_ERROR')
        self.request('GET', '/products?size=0', token=user, status=400, code='VALIDATION_ERROR')
        self.request('GET', '/products?status=BAD', token=user, status=400, code='VALIDATION_ERROR')
        body = dict(name='', price=0, stock=-1, category='', status='ACTIVE', description='x'*4001)
        error = self.request('POST', '/products', body, self.seller, 400, 'VALIDATION_ERROR')
        for field in ['name', 'price', 'stock', 'category', 'description']:
            self.assertIn(field, error['details'])
        self.request('POST', '/products', {}, user, 403, 'ACCESS_DENIED')
        self.request('DELETE', f"/products/{p['id']}", token=self.other_seller, status=403, code='ACCESS_DENIED')
        self.request('PUT', f"/products/{p['id']}", dict(name='Новая цена', price=120, stock=20, category=p['category'], status='ACTIVE'), self.seller)
        self.request('DELETE', f"/products/{p['id']}", token=self.admin, status=204)
        self.assertEqual('ARCHIVED', self.request('GET', f"/products/{p['id']}", token=user)['status'])
        self.assertEqual('1', sql(f"SELECT count(*) FROM products WHERE id={p['id']}"))
        self.request('GET', '/products/999999999', token=user, status=404, code='PRODUCT_NOT_FOUND')
        self.request('GET', '/products/no-id', token=user, status=400, code='VALIDATION_ERROR')

    def test_contract_rejects_wrong_json_types_before_business_logic(self):
        valid = dict(name='Товар', price=10, stock=1, category='types', status='ACTIVE')
        for field, value in [('name', 123), ('price', '10'), ('stock', 1.5), ('stock', '1'), ('status', 0)]:
            body = dict(valid, **{field: value})
            error = self.request('POST', '/products', body, self.seller, 400, 'VALIDATION_ERROR')
            self.assertIn(field, error['details'])
        self.request('POST', '/auth/register', {'username':'escalate-' + uuid.uuid4().hex, 'password':'test-password', 'role':'ADMIN'}, status=400, code='VALIDATION_ERROR')

    def test_encoded_paths_cannot_bypass_authentication(self):
        self.request('GET', '/%70roducts', status=401, code='TOKEN_INVALID')
        self.request('GET', '/%70roducts/1', status=401, code='TOKEN_INVALID')
        self.request('GET', '/swagger-ui/../products', status=401, code='TOKEN_INVALID')
        self.request('GET', '/openapi/%2e%2e/products', status=401, code='TOKEN_INVALID')

    def test_null_order_items_rejected_by_contract(self):
        _, user = self.user()
        error = self.request('POST', '/orders', {'items':[None]}, user, 400, 'VALIDATION_ERROR')
        self.assertIn('items[0]', error['details'])

    def test_swagger_ui_serves_the_written_contract(self):
        status, html, _ = call('GET', '/swagger-ui/index.html')
        self.assertEqual(200, status)
        self.assertIn('/openapi/marketplace.yaml', html)
        self.assertEqual(200, call('GET', '/swagger-assets/swagger-ui-bundle.js')[0])
        status, contract, _ = call('GET', '/openapi/marketplace.yaml')
        self.assertEqual(200, status)
        self.assertEqual((ROOT / 'openapi/marketplace.yaml').read_text(), contract)

    def test_request_logs_are_json_and_mask_sensitive_body(self):
        secret = 'private-' + uuid.uuid4().hex
        status, _, headers = call('POST', '/auth/login', {'username':'seller', 'password':secret})
        self.assertEqual(401, status)
        request_id = headers['X-Request-Id']
        raw = subprocess.check_output(['docker','compose','logs','--no-log-prefix','app'], cwd=ROOT, text=True)
        entries = [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
        entry = next(e for e in entries if e['request_id'] == request_id)
        self.assertEqual(('POST','/auth/login',401,None), (entry['method'],entry['endpoint'],entry['status_code'],entry['user_id']))
        self.assertGreaterEqual(entry['duration_ms'], 0)
        datetime.fromisoformat(entry['timestamp'].replace('Z','+00:00'))
        self.assertEqual('***', entry['request_body']['password'])
        self.assertNotIn(secret, raw)

    def test_decimal_amounts_and_datetime_contract(self):
        _, user = self.user()
        p = self.product(price=0.01, stock=999)
        order = self.order(user, p, 999)
        self.assertEqual(9.99, order['total_amount'])
        self.assertEqual('9.99', sql(f"SELECT total_amount FROM orders WHERE id={order['id']}"))
        now = datetime.now(timezone.utc)
        body = dict(code=uuid.uuid4().hex[:12].upper(), discount_type='PERCENTAGE', discount_value=20,
                    min_order_amount=0, max_uses=10, valid_from=123,
                    valid_until=(now+timedelta(days=1)).isoformat(), active=True)
        error = self.request('POST', '/promo-codes', body, self.seller, 400, 'VALIDATION_ERROR')
        self.assertIn('valid_from', error['details'])
        body['valid_from'] = '2026-01-01T00:00:00'
        error = self.request('POST', '/promo-codes', body, self.seller, 400, 'VALIDATION_ERROR')
        self.assertIn('valid_from', error['details'])

    def test_unicode_password_is_checked_in_full(self):
        username = 'u' + uuid.uuid4().hex
        password = 'я' * 36 + 'abcdefgh'
        self.request('POST', '/auth/register', {'username': username, 'password': password}, status=201)
        self.request('POST', '/auth/login', {'username': username, 'password': password})
        self.request('POST', '/auth/login', {'username': username, 'password': 'я' * 36 + 'otherpwd'},
                     status=401, code='TOKEN_INVALID')

    def test_database_numeric_overflow_uses_contract_error(self):
        body = dict(name='Товар', price=10000000000, stock=1, category='bounds', status='ACTIVE')
        self.request('POST', '/products', body, self.seller, 400, 'VALIDATION_ERROR')
        body.update(price=1, stock=2147483648)
        self.request('POST', '/products', body, self.seller, 400, 'VALIDATION_ERROR')

    def test_application_runs_with_python(self):
        result = subprocess.run(['docker', 'compose', 'exec', '-T', 'app', 'python', '-c',
                                 'import sys; assert sys.version_info.major == 3'],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)

    def test_auth_tokens_and_registration(self):
        uid, user = self.user()
        payload = json.loads(base64.urlsafe_b64decode(user.split('.')[1] + '=='))
        self.assertEqual('USER', payload['role'])
        self.assertEqual(900, payload['exp']-payload['iat'])
        data = self.request('POST', '/auth/login', {'username': 'seller', 'password': 'demo-password'})
        refreshed = self.request('POST', '/auth/refresh', {'refresh_token': data['refresh_token']})
        self.request('GET', '/products', token=refreshed['access_token'])
        self.request('GET', '/products', token=data['refresh_token'], status=401, code='TOKEN_INVALID')
        self.request('GET', '/products', token='invalid', status=401, code='TOKEN_INVALID')
        self.request('POST', '/auth/refresh', {'refresh_token': user}, status=401, code='REFRESH_TOKEN_INVALID')
        self.request('POST', '/auth/refresh', {'refresh_token': 'invalid'}, status=401, code='REFRESH_TOKEN_INVALID')
        self.request('POST', '/auth/login', {'username': 'seller', 'password': 'wrong'}, status=401, code='TOKEN_INVALID')
        # Просроченный access с корректной подписью: проверяется именно exp.
        payload['exp'] = payload['iat'] - 1
        enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=')
        unsigned = user.split('.')[0].encode() + b'.' + enc(json.dumps(payload).encode())
        secret = os.environ.get('JWT_SECRET', 'homework-local-secret-at-least-32-characters')
        expired = (unsigned + b'.' + enc(hmac.new(secret.encode(), unsigned, hashlib.sha256).digest())).decode()
        self.request('GET', '/products', token=expired, status=401, code='TOKEN_EXPIRED')

    def test_order_snapshot_limits_ownership_and_cancel(self):
        uid, user = self.user()
        _, stranger = self.user()
        p = self.product()
        o = self.order(user, p)
        self.assertEqual((200, 0, 'CREATED'), (o['total_amount'], o['discount_amount'], o['status']))
        self.assertEqual(18, self.request('GET', f"/products/{p['id']}", token=user)['stock'])
        self.request('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':1}]}, user, 429, 'ORDER_LIMIT_EXCEEDED')
        self.age(uid)
        self.request('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':1}]}, user, 409, 'ORDER_HAS_ACTIVE')
        self.request('GET', f"/orders/{o['id']}", token=stranger, status=403, code='ORDER_OWNERSHIP_VIOLATION')
        self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':1}]}, stranger, 403, 'ORDER_OWNERSHIP_VIOLATION')
        self.request('POST', f"/orders/{o['id']}/cancel", token=stranger, status=403, code='ORDER_OWNERSHIP_VIOLATION')
        self.request('GET', f"/orders/{o['id']}", token=self.seller, status=403, code='ACCESS_DENIED')
        self.request('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':1}]}, self.seller, 403, 'ACCESS_DENIED')
        self.request('PUT', f"/products/{p['id']}", dict(name='Товар', price=150, stock=18, category=p['category'], status='ACTIVE'), self.seller)
        self.assertEqual(100, self.request('GET', f"/orders/{o['id']}", token=user)['items'][0]['price_at_order'])
        updated = self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':3}]}, user)
        self.assertEqual(450, updated['total_amount'])
        self.assertEqual(17, self.request('GET', f"/products/{p['id']}", token=user)['stock'])
        self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':1}]}, user, 429, 'ORDER_LIMIT_EXCEEDED')
        self.request('POST', f"/orders/{o['id']}/cancel", token=self.admin)
        self.assertEqual('20', sql(f"SELECT stock FROM products WHERE id={p['id']}"))
        self.request('POST', f"/orders/{o['id']}/cancel", token=user, status=409, code='INVALID_STATE_TRANSITION')
        self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':1}]}, user, 409, 'INVALID_STATE_TRANSITION')
        self.request('GET', '/orders/999999999', token=user, status=404, code='ORDER_NOT_FOUND')

    def test_catalog_stock_validation_and_transaction_rollback(self):
        uid, user = self.user()
        p = self.product(stock=5)
        inactive = self.product(status='INACTIVE')
        for items, code, status in [([], 'VALIDATION_ERROR', 400),
                                   ([{'product_id':p['id'], 'quantity':0}], 'VALIDATION_ERROR', 400),
                                   ([{'product_id':p['id'], 'quantity':1000}], 'VALIDATION_ERROR', 400),
                                   ([{'product_id':p['id'], 'quantity':1}]*51, 'VALIDATION_ERROR', 400),
                                   ([{'product_id':999999999, 'quantity':1}], 'PRODUCT_NOT_FOUND', 404),
                                   ([{'product_id':inactive['id'], 'quantity':1}], 'PRODUCT_INACTIVE', 409),
                                   ([{'product_id':p['id'], 'quantity':6}], 'INSUFFICIENT_STOCK', 409),
                                   ([{'product_id':p['id'], 'quantity':3}]*2, 'INSUFFICIENT_STOCK', 409)]:
            self.request('POST', '/orders', {'items':items}, user, status, code)
        self.request('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':1}], 'promo_code':'bad'}, user, 400, 'VALIDATION_ERROR')
        self.request('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':1}], 'promo_code':'MISSING'}, user, 422, 'PROMO_CODE_INVALID')
        self.assertEqual('5', sql(f"SELECT stock FROM products WHERE id={p['id']}"))
        self.assertEqual('0', sql(f"SELECT count(*) FROM orders WHERE user_id={uid}"))
        o = self.order(user, p, 2)
        self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':6}]}, user, 409, 'INSUFFICIENT_STOCK')
        self.assertEqual('3', sql(f"SELECT stock FROM products WHERE id={p['id']}"))
        self.assertEqual(2, self.request('GET', f"/orders/{o['id']}", token=user)['items'][0]['quantity'])
        self.assertEqual('0', sql(f"SELECT count(*) FROM user_operations WHERE user_id={uid} AND operation_type='UPDATE_ORDER'"))

    def test_promo_recalculation_and_release(self):
        _, user = self.user()
        p = self.product()
        promo = self.promo(min_order_amount=200, max_uses=1)
        o = self.order(user, p, 3, promo_code=promo['code'])
        self.assertEqual((240, 60), (o['total_amount'], o['discount_amount']))
        updated = self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':2}]}, user)
        self.assertEqual((160, 40), (updated['total_amount'], updated['discount_amount']))
        uid = o['user_id']
        self.age(uid)
        updated = self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':1}]}, user)
        self.assertEqual((100, 0, None), (updated['total_amount'], updated['discount_amount'], updated['promo_code_id']))
        self.assertEqual('0', sql(f"SELECT current_uses FROM promo_codes WHERE id={promo['id']}"))
        for changes, expected in [({'discount_value':70}, (60, 140)),
                                  ({'discount_value':71}, (200, 0)),
                                  ({'discount_type':'FIXED_AMOUNT', 'discount_value':999}, (0, 200))]:
            _, another = self.user()
            promo2 = self.promo(**changes)
            discounted = self.order(another, p, 2, promo_code=promo2['code'])
            self.assertEqual(expected, (discounted['total_amount'], discounted['discount_amount']))
            self.request('POST', f"/orders/{discounted['id']}/cancel", token=another)
            self.assertEqual('0', sql(f"SELECT current_uses FROM promo_codes WHERE id={promo2['id']}"))

    def test_invalid_promo_and_order_states(self):
        p = self.product()
        now = datetime.now(timezone.utc)
        for changes, code in [({'active':False}, 'PROMO_CODE_INVALID'),
                              ({'max_uses':0}, 'PROMO_CODE_INVALID'),
                              ({'valid_from':(now-timedelta(days=2)).isoformat(), 'valid_until':(now-timedelta(days=1)).isoformat()}, 'PROMO_CODE_INVALID'),
                              ({'valid_from':(now+timedelta(days=1)).isoformat(), 'valid_until':(now+timedelta(days=2)).isoformat()}, 'PROMO_CODE_INVALID'),
                              ({'min_order_amount':201}, 'PROMO_CODE_MIN_AMOUNT')]:
            _, user = self.user()
            promo = self.promo(**changes)
            self.request('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':2}], 'promo_code':promo['code']}, user, 422, code)
        for state in ['PAYMENT_PENDING','PAID','SHIPPED','COMPLETED']:
            _, user = self.user()
            o = self.order(user, p, 1)
            sql(f"UPDATE orders SET status='{state}' WHERE id={o['id']}")
            self.request('PUT', f"/orders/{o['id']}", {'items':[{'product_id':p['id'], 'quantity':1}]}, user, 409, 'INVALID_STATE_TRANSITION')
            if state == 'PAYMENT_PENDING':
                self.request('POST', f"/orders/{o['id']}/cancel", token=user)
            else:
                self.request('POST', f"/orders/{o['id']}/cancel", token=user, status=409, code='INVALID_STATE_TRANSITION')

    def test_concurrent_reservation_cannot_oversell(self):
        p = self.product(stock=1)
        users = [self.user()[1], self.user()[1]]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            responses = list(pool.map(lambda token: call('POST', '/orders', {'items':[{'product_id':p['id'], 'quantity':1}]}, token), users))
        self.assertEqual([201, 409], sorted(r[0] for r in responses))
        self.assertEqual('0', sql(f"SELECT stock FROM products WHERE id={p['id']}"))
        self.assertEqual('1', sql(f"SELECT count(*) FROM order_items WHERE product_id={p['id']}"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
