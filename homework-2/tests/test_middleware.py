"""Проверка контрактного ответа и JSON-лога при внутренней ошибке."""
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
import uuid

from app.middleware import ApiMiddleware


class MiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_internal_error_has_request_id_and_json_log(self):
        async def broken_app(scope, receive, send):
            raise RuntimeError('Внутренняя ошибка')

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        sent = []

        async def send(message):
            sent.append(message)

        scope = {'type': 'http', 'path': '/auth/login', 'method': 'POST', 'headers': []}
        output = StringIO()
        with redirect_stdout(output):
            await ApiMiddleware(broken_app)(scope, receive, send)
        start, body = sent
        self.assertEqual(500, start['status'])
        headers = dict(start['headers'])
        request_id = headers[b'x-request-id'].decode()
        uuid.UUID(request_id)
        self.assertEqual('INTERNAL_ERROR', json.loads(body['body'])['error_code'])
        entry = json.loads(output.getvalue())
        self.assertEqual((request_id, 500), (entry['request_id'], entry['status_code']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
