"""gRPC-сервис рейсов и резервирования мест."""
from concurrent import futures
from datetime import timezone
import logging
import os
import re
from threading import Lock
from uuid import UUID

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

import flight_pb2 as pb
import flight_pb2_grpc as rpc
from app.cache import FlightCache
from app.db import connection

FLIGHTS = """
    SELECT flights.*, airlines.name AS airline
    FROM flights JOIN airlines ON flights.airline_id = airlines.id
"""
RESERVATION = 'SELECT * FROM seat_reservations WHERE booking_id = %s'


def timestamp(value):
    result = Timestamp()
    result.FromDatetime(value)
    return result


def flight_message(row):
    return pb.Flight(
        id=row['id'], flight_number=row['flight_number'], airline=row['airline'],
        origin=row['origin'], destination=row['destination'],
        departure_at=timestamp(row['departure_at']), arrival_at=timestamp(row['arrival_at']),
        total_seats=row['total_seats'], available_seats=row['available_seats'],
        price=row['price'], status=pb.FlightStatus.Value(row['status']),
    )


def reservation_message(row):
    return pb.SeatReservation(
        booking_id=str(row['booking_id']), flight_id=row['flight_id'],
        seat_count=row['seat_count'], status=pb.ReservationStatus.Value(row['status']),
    )


def search_key(origin, destination, day):
    return f'search:{origin}:{destination}:{day or "all"}'


def flight_key(flight_id):
    return f'flight:{flight_id}'


def booking_uuid(value, context):
    try:
        return UUID(value)
    except ValueError:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Некорректный booking_id')


def lock_flight(conn, flight_id, context):
    if flight_id <= 0:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Некорректный flight_id')
    row = conn.execute(
        'SELECT * FROM flights WHERE id = %s FOR UPDATE', (flight_id,)
    ).fetchone()
    if row is None:
        context.abort(grpc.StatusCode.NOT_FOUND, 'Рейс не найден')
    return row


class FlightService(rpc.FlightServiceServicer):
    def __init__(self):
        self.api_key = os.environ['FLIGHT_API_KEY']
        self.cache = FlightCache()
        # Не даём параллельному cache miss записать старое значение после инвалидации.
        self.cache_lock = Lock()

    def authenticate(self, context):
        if not self.api_key or dict(context.invocation_metadata()).get('x-api-key') != self.api_key:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, 'Некорректный API-ключ')

    def invalidate(self, row):
        origin, destination = row['origin'], row['destination']
        day = row['departure_at'].astimezone(timezone.utc).date()
        self.cache.invalidate(
            flight_key(row['id']), search_key(origin, destination, day),
            search_key(origin, destination, None),
        )

    def SearchFlights(self, request, context):
        self.authenticate(context)
        origin, destination = request.origin, request.destination
        if not re.fullmatch('[A-Z]{3}', origin) or not re.fullmatch('[A-Z]{3}', destination):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Укажите IATA-коды аэропортов')
        day = None
        if request.HasField('date'):
            try:
                day = request.date.ToDatetime(tzinfo=timezone.utc).date()
            except (ValueError, OverflowError):
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Некорректная дата вылета')

        def load():
            query = FLIGHTS + " WHERE origin = %s AND destination = %s AND status = 'SCHEDULED'"
            params = [origin, destination]
            if day is not None:
                query += " AND (departure_at AT TIME ZONE 'UTC')::date = %s"
                params.append(day)
            with connection() as conn:
                rows = conn.execute(query + ' ORDER BY departure_at, flights.id', params).fetchall()
            return pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])

        with self.cache_lock:
            return self.cache.get(search_key(origin, destination, day), pb.SearchFlightsResponse, load)

    def GetFlight(self, request, context):
        self.authenticate(context)
        if request.flight_id <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Некорректный flight_id')

        def load():
            with connection() as conn:
                row = conn.execute(FLIGHTS + ' WHERE flights.id = %s', (request.flight_id,)).fetchone()
            if row is None:
                context.abort(grpc.StatusCode.NOT_FOUND, 'Рейс не найден')
            return flight_message(row)

        with self.cache_lock:
            return self.cache.get(flight_key(request.flight_id), pb.Flight, load)

    def ReserveSeats(self, request, context):
        self.authenticate(context)
        booking_id = booking_uuid(request.booking_id, context)
        if request.seat_count <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Количество мест должно быть положительным')

        with self.cache_lock:
            with connection() as conn:
                flight = lock_flight(conn, request.flight_id, context)
                row = conn.execute(RESERVATION, (booking_id,)).fetchone()
                if row is None:
                    if flight['status'] != 'SCHEDULED':
                        context.abort(grpc.StatusCode.FAILED_PRECONDITION, 'Рейс вне расписания')
                    if flight['available_seats'] < request.seat_count:
                        context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, 'Недостаточно мест')
                    if not context.is_active():
                        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, 'Время запроса истекло')
                    row = conn.execute(
                        """INSERT INTO seat_reservations (booking_id, flight_id, seat_count, status)
                           VALUES (%s, %s, %s, 'ACTIVE')
                           ON CONFLICT (booking_id) DO NOTHING RETURNING *""",
                        (booking_id, request.flight_id, request.seat_count),
                    ).fetchone()
                    if row is not None:
                        conn.execute(
                            'UPDATE flights SET available_seats = available_seats - %s WHERE id = %s',
                            (request.seat_count, request.flight_id),
                        )
                    else:
                        row = conn.execute(RESERVATION, (booking_id,)).fetchone()
                if (row['flight_id'], row['seat_count']) != (request.flight_id, request.seat_count):
                    context.abort(grpc.StatusCode.ALREADY_EXISTS, 'booking_id уже используется')
            self.invalidate(flight)
        return reservation_message(row)

    def ReleaseReservation(self, request, context):
        self.authenticate(context)
        booking_id = booking_uuid(request.booking_id, context)

        with self.cache_lock:
            with connection() as conn:
                row = conn.execute(RESERVATION, (booking_id,)).fetchone()
                if row is None:
                    context.abort(grpc.StatusCode.NOT_FOUND, 'Резервация не найдена')
                flight = lock_flight(conn, row['flight_id'], context)
                row = conn.execute(RESERVATION + ' FOR UPDATE', (booking_id,)).fetchone()
                if row['status'] == 'ACTIVE':
                    conn.execute(
                        'UPDATE flights SET available_seats = available_seats + %s WHERE id = %s',
                        (row['seat_count'], row['flight_id']),
                    )
                    row = conn.execute(
                        "UPDATE seat_reservations SET status = 'RELEASED' WHERE booking_id = %s RETURNING *",
                        (booking_id,),
                    ).fetchone()
            self.invalidate(flight)
        return reservation_message(row)

    def UpdateFlight(self, request, context):
        self.authenticate(context)
        if request.price <= 0 or request.status not in (
            pb.SCHEDULED, pb.DEPARTED, pb.CANCELLED, pb.COMPLETED,
        ):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Некорректная цена или статус')

        with self.cache_lock:
            with connection() as conn:
                lock_flight(conn, request.flight_id, context)
                conn.execute(
                    'UPDATE flights SET price = %s, status = %s WHERE id = %s',
                    (request.price, pb.FlightStatus.Name(request.status), request.flight_id),
                )
                row = conn.execute(FLIGHTS + ' WHERE flights.id = %s', (request.flight_id,)).fetchone()
            self.invalidate(row)
        return flight_message(row)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    rpc.add_FlightServiceServicer_to_server(FlightService(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    server.wait_for_termination()
