"""Клиент Flight Service с повторами и Circuit Breaker."""
from collections import deque
import logging
import os
from threading import Lock
from time import monotonic, sleep

import grpc

import flight_pb2_grpc as rpc

log = logging.getLogger('uvicorn.error')
RETRYABLE = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}


class CircuitOpen(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return 'Flight Service временно недоступен'


class CircuitBreaker(grpc.UnaryUnaryClientInterceptor):
    def __init__(self):
        self.threshold = int(os.environ['CIRCUIT_FAILURE_THRESHOLD'])
        self.timeout = float(os.environ['CIRCUIT_OPEN_SECONDS'])
        self.window = float(os.environ['CIRCUIT_WINDOW_SECONDS'])
        self.state = 'CLOSED'
        self.opened_at = 0
        self.failures = deque()
        self.lock = Lock()
        self.generation = 0

    def transition(self, state):
        log.info('Circuit Breaker %s -> %s', self.state, state)
        self.state = state
        self.generation += 1
        if state == 'OPEN':
            self.opened_at = monotonic()
        if state == 'CLOSED':
            self.failures.clear()

    def begin(self):
        with self.lock:
            if self.state == 'HALF_OPEN':
                raise CircuitOpen()
            if self.state == 'OPEN':
                if monotonic() - self.opened_at < self.timeout:
                    raise CircuitOpen()
                self.transition('HALF_OPEN')
            return self.generation, self.state == 'HALF_OPEN'

    def finish(self, generation, failed):
        with self.lock:
            if self.generation != generation:
                return
            if self.state == 'HALF_OPEN':
                self.transition('OPEN' if failed else 'CLOSED')
            elif failed:
                now = monotonic()
                while self.failures and now - self.failures[0] > self.window:
                    self.failures.popleft()
                self.failures.append(now)
                if len(self.failures) >= self.threshold:
                    self.transition('OPEN')

    def intercept_unary_unary(self, continuation, call_details, request):
        generation, probe = self.begin()
        attempts = 1 if probe else 3
        for attempt in range(attempts):
            try:
                outcome = continuation(call_details, request)
                outcome.result()
            except grpc.RpcError as error:
                retryable = error.code() in RETRYABLE
                if retryable and attempt + 1 < attempts:
                    sleep(0.1 * 2 ** attempt)
                    with self.lock:
                        if self.generation != generation:
                            raise CircuitOpen() from error
                    continue
                self.finish(generation, probe or retryable)
                raise
            self.finish(generation, False)
            return outcome


class FlightClient:
    def __init__(self):
        channel = grpc.insecure_channel(
            os.environ['FLIGHT_ADDRESS'], options=[('grpc.enable_retries', 0)],
        )
        self.channel = grpc.intercept_channel(channel, CircuitBreaker())
        self.stub = rpc.FlightServiceStub(self.channel)
        self.metadata = (('x-api-key', os.environ['FLIGHT_API_KEY']),)

    def call(self, method, request):
        return method(request, metadata=self.metadata, timeout=1.0)
