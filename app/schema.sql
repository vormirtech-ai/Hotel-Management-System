-- Hotel Management System schema (SQLite). Safe to run repeatedly.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'staff')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Tax rules. A rule applies to a bill line when the category matches and the
-- unit rate (e.g. per-night tariff) falls inside [min_amount, max_amount].
CREATE TABLE IF NOT EXISTS taxes (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    rate       REAL NOT NULL CHECK (rate >= 0),
    applies_to TEXT NOT NULL CHECK (applies_to IN ('room', 'venue', 'extra', 'all')),
    min_amount REAL NOT NULL DEFAULT 0,
    max_amount REAL,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS floors (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    level INTEGER NOT NULL UNIQUE,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS room_types (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    base_rate      REAL NOT NULL CHECK (base_rate >= 0),
    max_occupancy  INTEGER NOT NULL DEFAULT 2,
    extra_bed_rate REAL NOT NULL DEFAULT 0,
    amenities      TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rooms (
    id           INTEGER PRIMARY KEY,
    number       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    floor_id     INTEGER NOT NULL REFERENCES floors(id) ON DELETE RESTRICT,
    room_type_id INTEGER NOT NULL REFERENCES room_types(id) ON DELETE RESTRICT,
    rate         REAL,  -- NULL = use room type base rate
    status       TEXT NOT NULL DEFAULT 'available'
                 CHECK (status IN ('available', 'occupied', 'cleaning', 'maintenance')),
    active       INTEGER NOT NULL DEFAULT 1,
    notes        TEXT NOT NULL DEFAULT ''
);

-- Banquet halls and lawns.
CREATE TABLE IF NOT EXISTS venues (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind          TEXT NOT NULL CHECK (kind IN ('banquet', 'lawn')),
    capacity      INTEGER NOT NULL DEFAULT 0,
    area_sqft     INTEGER NOT NULL DEFAULT 0,
    full_day_rate REAL NOT NULL DEFAULT 0,
    half_day_rate REAL NOT NULL DEFAULT 0,
    description   TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS guests (
    id         INTEGER PRIMARY KEY,
    full_name  TEXT NOT NULL,
    phone      TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    address    TEXT NOT NULL DEFAULT '',
    id_type    TEXT NOT NULL DEFAULT '',
    id_number  TEXT NOT NULL DEFAULT '',
    company    TEXT NOT NULL DEFAULT '',
    gstin      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookings (
    id             INTEGER PRIMARY KEY,
    code           TEXT UNIQUE,
    guest_id       INTEGER NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    room_id        INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
    check_in       TEXT NOT NULL,  -- YYYY-MM-DD
    check_out      TEXT NOT NULL,  -- YYYY-MM-DD, exclusive (departure day)
    adults         INTEGER NOT NULL DEFAULT 1,
    children       INTEGER NOT NULL DEFAULT 0,
    rate           REAL NOT NULL,  -- per night
    extra_beds     INTEGER NOT NULL DEFAULT 0,
    extra_bed_rate REAL NOT NULL DEFAULT 0,
    discount       REAL NOT NULL DEFAULT 0,  -- whole-stay amount
    status         TEXT NOT NULL DEFAULT 'reserved'
                   CHECK (status IN ('reserved', 'checked_in', 'checked_out', 'cancelled', 'no_show')),
    source         TEXT NOT NULL DEFAULT 'walk-in',
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checked_in_at  TEXT,
    checked_out_at TEXT,
    cancelled_at   TEXT,
    CHECK (check_out > check_in)
);
CREATE INDEX IF NOT EXISTS idx_bookings_dates ON bookings(check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_bookings_room ON bookings(room_id);

CREATE TABLE IF NOT EXISTS venue_bookings (
    id           INTEGER PRIMARY KEY,
    code         TEXT UNIQUE,
    guest_id     INTEGER NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    venue_id     INTEGER NOT NULL REFERENCES venues(id) ON DELETE RESTRICT,
    event_date   TEXT NOT NULL,
    slot         TEXT NOT NULL CHECK (slot IN ('full', 'morning', 'evening')),
    event_name   TEXT NOT NULL DEFAULT '',
    event_type   TEXT NOT NULL DEFAULT '',
    pax          INTEGER NOT NULL DEFAULT 0,
    rate         REAL NOT NULL,
    discount     REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'confirmed'
                 CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_venue_bookings_date ON venue_bookings(event_date);

-- Extra charges (food, laundry, decoration, late checkout fee ...).
CREATE TABLE IF NOT EXISTS charges (
    id               INTEGER PRIMARY KEY,
    booking_id       INTEGER REFERENCES bookings(id) ON DELETE CASCADE,
    venue_booking_id INTEGER REFERENCES venue_bookings(id) ON DELETE CASCADE,
    charge_date      TEXT NOT NULL,
    description      TEXT NOT NULL,
    tax_class        TEXT NOT NULL DEFAULT 'extra' CHECK (tax_class IN ('room', 'venue', 'extra')),
    quantity         REAL NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price       REAL NOT NULL CHECK (unit_price >= 0),
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((booking_id IS NULL) <> (venue_booking_id IS NULL))
);

-- Issued invoices keep a snapshot of lines, taxes, hotel and guest details so
-- later changes to settings never alter an issued bill.
CREATE TABLE IF NOT EXISTS invoices (
    id               INTEGER PRIMARY KEY,
    number           TEXT NOT NULL UNIQUE,
    booking_id       INTEGER REFERENCES bookings(id),
    venue_booking_id INTEGER REFERENCES venue_bookings(id),
    guest_id         INTEGER NOT NULL REFERENCES guests(id),
    issue_date       TEXT NOT NULL,
    subtotal         REAL NOT NULL,
    discount         REAL NOT NULL,
    taxable          REAL NOT NULL,
    tax_total        REAL NOT NULL,
    round_off        REAL NOT NULL,
    total            REAL NOT NULL,
    lines_json       TEXT NOT NULL,
    taxes_json       TEXT NOT NULL,
    hotel_json       TEXT NOT NULL,
    guest_json       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('issued', 'void')),
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(issue_date);
-- At most one live (non-void) invoice per booking / event.
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_booking ON invoices(booking_id) WHERE status = 'issued' AND booking_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_venue ON invoices(venue_booking_id) WHERE status = 'issued' AND venue_booking_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS payments (
    id               INTEGER PRIMARY KEY,
    booking_id       INTEGER REFERENCES bookings(id) ON DELETE CASCADE,
    venue_booking_id INTEGER REFERENCES venue_bookings(id) ON DELETE CASCADE,
    amount           REAL NOT NULL,
    method           TEXT NOT NULL CHECK (method IN ('cash', 'card', 'upi', 'bank', 'other')),
    reference        TEXT NOT NULL DEFAULT '',
    paid_on          TEXT NOT NULL,
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((booking_id IS NULL) <> (venue_booking_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(paid_on);
