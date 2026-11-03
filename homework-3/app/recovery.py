"""Компенсация незавершённых бронирований после сбоя."""
import logging

import grpc
import psycopg

import flight_pb2 as pb
from app.db import connection

log = logging.getLogger('uvicorn.error')


def recover_one(client):
    with connection() as conn:
        pending = conn.execute(
            """SELECT booking_id FROM reservation_recovery
               WHERE recover_after <= now() ORDER BY recover_after
               LIMIT 1 FOR UPDATE SKIP LOCKED"""
        ).fetchone()
        if pending is None:
            return
        booking_id = pending['booking_id']
        try:
            client.call(client.stub.ReleaseReservation,
                        pb.ReleaseReservationRequest(booking_id=str(booking_id)))
        except grpc.RpcError as error:
            if error.code() != grpc.StatusCode.NOT_FOUND:
                conn.execute(
                    "UPDATE reservation_recovery SET recover_after = now() + interval '5 seconds' "
                    "WHERE booking_id = %s", (booking_id,),
                )
                log.warning('Компенсация %s отложена: %s', booking_id, error.details())
                return
        conn.execute('DELETE FROM reservation_recovery WHERE booking_id = %s', (booking_id,))


def recover_reservations(client, stop):
    while not stop.wait(1):
        try:
            recover_one(client)
        except psycopg.Error as error:
            log.warning('Не удалось обработать компенсацию: %s', error)
