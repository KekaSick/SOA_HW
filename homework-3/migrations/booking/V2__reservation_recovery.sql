CREATE TABLE reservation_recovery (
    booking_id uuid PRIMARY KEY,
    recover_after timestamptz NOT NULL
);
