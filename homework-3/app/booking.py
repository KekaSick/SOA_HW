"""REST API поиска рейсов и бронирования."""
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from threading import Event, Thread
from typing import Annotated
from uuid import UUID, uuid4

import grpc
import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

import flight_pb2 as pb
from app.db import connection
from app.flight_client import FlightClient
from app.recovery import recover_reservations

RESERVATION_REJECTIONS = {
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.FAILED_PRECONDITION,
    grpc.StatusCode.ALREADY_EXISTS,
    grpc.StatusCode.UNAUTHENTICATED,
}
GRPC_ERRORS = {
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.RESOURCE_EXHAUSTED: 409,
    grpc.StatusCode.FAILED_PRECONDITION: 409,
    grpc.StatusCode.ALREADY_EXISTS: 409,
    grpc.StatusCode.UNAVAILABLE: 503,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
}


@asynccontextmanager
async def lifespan(app):
    app.state.flight = FlightClient()
    stop = Event()
    recovery = Thread(target=recover_reservations, args=(app.state.flight, stop))
    recovery.start()
    try:
        yield
    finally:
        stop.set()
        recovery.join()
        app.state.flight.channel.close()


app = FastAPI(title='Booking Service', lifespan=lifespan)
PositiveId = Annotated[int, Field(gt=0, le=2**63 - 1)]


class BookingRequest(BaseModel):
    user_id: PositiveId
    flight_id: PositiveId
    passenger_name: str = Field(pattern=r'\S')
    passenger_email: str = Field(pattern=r'^[^\s@]+@[^\s@]+$')
    seat_count: int = Field(gt=0, le=2**31 - 1)


def flight_json(flight):
    return {
        'id': flight.id,
        'flight_number': flight.flight_number,
        'airline': flight.airline,
        'origin': flight.origin,
        'destination': flight.destination,
        'departure_at': flight.departure_at.ToJsonString(),
        'arrival_at': flight.arrival_at.ToJsonString(),
        'total_seats': flight.total_seats,
        'available_seats': flight.available_seats,
        'price': flight.price,
        'status': pb.FlightStatus.Name(flight.status),
    }


def booking_json(row):
    return {**row, 'total_price': int(row['total_price'])}


def rpc_error(error):
    return HTTPException(GRPC_ERRORS.get(error.code(), 502), error.details())


def call_flight(client, method, message):
    try:
        return client.call(method, message)
    except grpc.RpcError as error:
        raise rpc_error(error) from error


@app.get('/flights')
def search_flights(request: Request, origin: str, destination: str,
                   day: Annotated[date | None, Query(alias='date')] = None):
    message = pb.SearchFlightsRequest(origin=origin, destination=destination)
    if day is not None:
        message.date.FromDatetime(datetime.combine(day, time.min, timezone.utc))
    client = request.app.state.flight
    result = call_flight(client, client.stub.SearchFlights, message)
    return [flight_json(flight) for flight in result.flights]


@app.get('/flights/{id}')
def get_flight(id: PositiveId, request: Request):
    client = request.app.state.flight
    result = call_flight(client, client.stub.GetFlight, pb.GetFlightRequest(flight_id=id))
    return flight_json(result)


@app.post('/bookings', status_code=201)
def create_booking(body: BookingRequest, request: Request):
    client = request.app.state.flight
    booking_id = uuid4()
    flight = call_flight(client, client.stub.GetFlight, pb.GetFlightRequest(flight_id=body.flight_id))
    with connection() as conn:
        conn.execute(
            """INSERT INTO reservation_recovery (booking_id, recover_after)
               VALUES (%s, now() + interval '5 seconds')""", (booking_id,),
        )

    reservation_error = None
    try:
        with connection() as conn:
            pending = conn.execute(
                'SELECT booking_id FROM reservation_recovery WHERE booking_id = %s FOR UPDATE',
                (booking_id,),
            ).fetchone()
            if pending is None:
                raise HTTPException(503, 'Время создания бронирования истекло')
            try:
                client.call(client.stub.ReserveSeats, pb.ReserveSeatsRequest(
                    flight_id=body.flight_id, seat_count=body.seat_count, booking_id=str(booking_id),
                ))
            except grpc.RpcError as error:
                if error.code() not in RESERVATION_REJECTIONS:
                    raise rpc_error(error) from error
                reservation_error = error
            if reservation_error is None:
                booking = conn.execute(
                    """INSERT INTO bookings (id, user_id, flight_id, passenger_name, passenger_email,
                                             seat_count, total_price, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'CONFIRMED') RETURNING *""",
                    (booking_id, body.user_id, body.flight_id, body.passenger_name,
                     body.passenger_email, body.seat_count, body.seat_count * flight.price),
                ).fetchone()
            conn.execute('DELETE FROM reservation_recovery WHERE booking_id = %s', (booking_id,))
    except psycopg.Error as error:
        # Запись бронирования и удаление задачи компенсации фиксируются вместе.
        raise HTTPException(500, 'Не удалось сохранить бронирование') from error
    if reservation_error is not None:
        raise rpc_error(reservation_error)
    return booking_json(booking)


def require_booking(conn, booking_id, lock=False):
    query = 'SELECT * FROM bookings WHERE id = %s'
    if lock:
        query += ' FOR UPDATE'
    booking = conn.execute(query, (booking_id,)).fetchone()
    if booking is None:
        raise HTTPException(404, 'Бронирование не найдено')
    return booking


@app.get('/bookings/{id}')
def get_booking(id: UUID):
    with connection() as conn:
        return booking_json(require_booking(conn, id))


@app.post('/bookings/{id}/cancel')
def cancel_booking(id: UUID, request: Request):
    client = request.app.state.flight
    with connection() as conn:
        booking = require_booking(conn, id, lock=True)
        if booking['status'] != 'CONFIRMED':
            raise HTTPException(409, 'Бронирование уже отменено')
        call_flight(client, client.stub.ReleaseReservation, pb.ReleaseReservationRequest(booking_id=str(id)))
        booking = conn.execute(
            "UPDATE bookings SET status = 'CANCELLED' WHERE id = %s RETURNING *", (id,),
        ).fetchone()
    return booking_json(booking)


@app.get('/bookings')
def list_bookings(user_id: PositiveId):
    with connection() as conn:
        rows = conn.execute('SELECT * FROM bookings WHERE user_id = %s ORDER BY id', (user_id,)).fetchall()
    return [booking_json(row) for row in rows]
