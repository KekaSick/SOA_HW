CREATE TABLE bookings (
    id uuid PRIMARY KEY,
    user_id bigint NOT NULL CHECK (user_id > 0),
    flight_id bigint NOT NULL CHECK (flight_id > 0),
    passenger_name text NOT NULL CHECK (btrim(passenger_name) <> ''),
    passenger_email text NOT NULL CHECK (btrim(passenger_email) <> ''),
    seat_count integer NOT NULL CHECK (seat_count > 0),
    total_price numeric(29, 0) NOT NULL CHECK (total_price > 0),
    status text NOT NULL CHECK (status IN ('CONFIRMED', 'CANCELLED'))
);
