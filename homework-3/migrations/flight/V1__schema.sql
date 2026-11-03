CREATE TABLE airlines (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE flights (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    flight_number text NOT NULL,
    airline_id bigint NOT NULL REFERENCES airlines(id),
    origin text NOT NULL CHECK (origin ~ '^[A-Z]{3}$'),
    destination text NOT NULL CHECK (destination ~ '^[A-Z]{3}$' AND origin <> destination),
    departure_at timestamptz NOT NULL,
    arrival_at timestamptz NOT NULL CHECK (arrival_at > departure_at),
    total_seats integer NOT NULL CHECK (total_seats > 0),
    available_seats integer NOT NULL CHECK (available_seats BETWEEN 0 AND total_seats),
    price bigint NOT NULL CHECK (price > 0),
    status text NOT NULL CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED'))
);

CREATE UNIQUE INDEX flights_number_day ON flights (flight_number, ((departure_at AT TIME ZONE 'UTC')::date));

CREATE TABLE seat_reservations (
    booking_id uuid PRIMARY KEY,
    flight_id bigint NOT NULL REFERENCES flights(id),
    seat_count integer NOT NULL CHECK (seat_count > 0),
    status text NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED'))
);
