"""Front-desk business logic that touches the database."""
import json
from datetime import date, datetime, timedelta

from . import billing

OPEN_STAY = ("reserved", "checked_in")


class BusinessError(Exception):
    """A rule violation to show the user (double booking, wrong state ...)."""


def now_text():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def today():
    return date.today().isoformat()


ROOM_SQL = """
    SELECT r.*, rt.name AS type_name, rt.max_occupancy, rt.extra_bed_rate,
           COALESCE(r.rate, rt.base_rate) AS eff_rate, f.name AS floor_name, f.level
    FROM rooms r
    JOIN room_types rt ON rt.id = r.room_type_id
    JOIN floors f ON f.id = r.floor_id
"""


def available_rooms(db, check_in, check_out, exclude_booking=None):
    """Active, non-maintenance rooms free for [check_in, check_out).

    An in-house guest blocks the room at least until tomorrow, even if they
    overstay their planned departure.
    """
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return db.execute(ROOM_SQL + """
        WHERE r.active = 1 AND r.status != 'maintenance'
          AND NOT EXISTS (
              SELECT 1 FROM bookings b
              WHERE b.room_id = r.id AND b.status IN ('reserved', 'checked_in')
                AND b.id != :exclude
                AND b.check_in < :out
                AND (CASE WHEN b.status = 'checked_in' THEN MAX(b.check_out, :tomorrow)
                          ELSE b.check_out END) > :in)
        ORDER BY f.level, r.number
    """, {"in": check_in, "out": check_out, "tomorrow": tomorrow, "exclude": exclude_booking or 0}).fetchall()


def ensure_room_free(db, room_id, check_in, check_out, exclude_booking=None):
    if not any(r["id"] == room_id for r in available_rooms(db, check_in, check_out, exclude_booking)):
        raise BusinessError("That room is not available for the selected dates.")


def validate_stay_dates(check_in, check_out):
    try:
        ci, co = date.fromisoformat(check_in), date.fromisoformat(check_out)
    except (TypeError, ValueError):
        raise BusinessError("Please enter valid check-in and check-out dates.")
    if co <= ci:
        raise BusinessError("Check-out must be after check-in.")
    if (co - ci).days > 365:
        raise BusinessError("A stay cannot be longer than 365 nights.")
    return ci, co


def venue_conflict(db, venue_id, event_date, slot, exclude=None):
    rows = db.execute(
        "SELECT slot FROM venue_bookings WHERE venue_id = ? AND event_date = ? "
        "AND status != 'cancelled' AND id != ?",
        (venue_id, event_date, exclude or 0),
    ).fetchall()
    return any(slot == "full" or r["slot"] == "full" or r["slot"] == slot for r in rows)


def get_booking(db, booking_id):
    row = db.execute("""
        SELECT b.*, r.number AS room_number, r.status AS room_status, rt.name AS type_name,
               rt.max_occupancy, f.name AS floor_name, g.full_name AS guest_name, g.phone AS guest_phone
        FROM bookings b
        JOIN rooms r ON r.id = b.room_id
        JOIN room_types rt ON rt.id = r.room_type_id
        JOIN floors f ON f.id = r.floor_id
        JOIN guests g ON g.id = b.guest_id
        WHERE b.id = ?""", (booking_id,)).fetchone()
    if row is None:
        raise BusinessError("Booking not found.")
    return row


def get_venue_booking(db, vb_id):
    row = db.execute(
        "SELECT vb.*, v.name AS venue_name, v.kind AS venue_kind, v.capacity, "
        "g.full_name AS guest_name, g.phone AS guest_phone FROM venue_bookings vb "
        "JOIN venues v ON v.id = vb.venue_id JOIN guests g ON g.id = vb.guest_id WHERE vb.id = ?",
        (vb_id,)).fetchone()
    if row is None:
        raise BusinessError("Event booking not found.")
    return row


def _ref(kind):
    return ("booking_id", "bookings") if kind == "booking" else ("venue_booking_id", "venue_bookings")


def charges_for(db, kind, record_id):
    col, _ = _ref(kind)
    return db.execute(f"SELECT * FROM charges WHERE {col} = ? ORDER BY charge_date, id", (record_id,)).fetchall()


def payments_for(db, kind, record_id):
    col, _ = _ref(kind)
    return db.execute(f"SELECT * FROM payments WHERE {col} = ? ORDER BY paid_on, id", (record_id,)).fetchall()


def live_invoice(db, kind, record_id):
    col, _ = _ref(kind)
    return db.execute(f"SELECT * FROM invoices WHERE {col} = ? AND status = 'issued'", (record_id,)).fetchone()


def taxes(db):
    return db.execute("SELECT * FROM taxes").fetchall()


def compute(db, kind, record, settings):
    """Current bill for a booking or event (not yet saved)."""
    charges = charges_for(db, kind, record["id"])
    if kind == "booking":
        lines = billing.booking_lines(record, f"Room {record['room_number']} – {record['type_name']}", charges, settings)
    else:
        lines = billing.venue_lines(record, record["venue_name"], charges, settings)
    return billing.compute_bill(lines, taxes(db), settings.get("round_off") == "1")


def paid_total(db, kind, record_id):
    col, _ = _ref(kind)
    return db.execute(f"SELECT COALESCE(SUM(amount), 0) FROM payments WHERE {col} = ?", (record_id,)).fetchone()[0]


def issue_invoice(db, kind, record, settings):
    """Freeze the current bill as a numbered invoice. Caller commits."""
    if live_invoice(db, kind, record["id"]):
        raise BusinessError("An invoice has already been issued for this booking.")
    bill = compute(db, kind, record, settings)
    guest = db.execute("SELECT * FROM guests WHERE id = ?", (record["guest_id"],)).fetchone()
    # Read-increment-write of the counter happens inside the caller's write
    # transaction (BEGIN IMMEDIATE), so two desks never get the same number.
    nxt = int(db.execute("SELECT value FROM settings WHERE key = 'invoice_next_number'").fetchone()[0] or 1)
    prefix = settings.get("invoice_prefix", "INV-")
    while db.execute("SELECT 1 FROM invoices WHERE number = ?", (f"{prefix}{nxt:05d}",)).fetchone():
        nxt += 1
    number = f"{prefix}{nxt:05d}"
    db.execute("UPDATE settings SET value = ? WHERE key = 'invoice_next_number'", (str(nxt + 1),))
    hotel = {k: v for k, v in settings.items() if k.startswith("hotel_") or k in ("currency_symbol", "invoice_terms")}
    col, _ = _ref(kind)
    cur = db.execute(
        f"INSERT INTO invoices ({col}, number, guest_id, issue_date, subtotal, discount, taxable, tax_total, "
        "round_off, total, lines_json, taxes_json, hotel_json, guest_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (record["id"], number, record["guest_id"], today(), bill["subtotal"], bill["discount"],
         bill["taxable"], bill["tax_total"], bill["round_off"], bill["total"],
         json.dumps(bill["lines"]), json.dumps(bill["tax_summary"]), json.dumps(hotel),
         json.dumps(dict(guest)), now_text()),
    )
    return cur.lastrowid


def begin(db):
    """Start a write transaction that locks out other writers until commit."""
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
