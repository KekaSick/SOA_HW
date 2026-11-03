INSERT INTO airlines (name) VALUES ('Аэрофлот');

INSERT INTO flights (flight_number, airline_id, origin, destination, departure_at,
                     arrival_at, total_seats, available_seats, price, status)
SELECT 'SU1234', id, 'SVO', 'LED', '2026-04-01 08:00:00+00',
       '2026-04-01 09:30:00+00', 100, 100, 650000, 'SCHEDULED'
FROM airlines WHERE name = 'Аэрофлот';
