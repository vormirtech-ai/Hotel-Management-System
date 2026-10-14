"""Daily progress report and date-range reports."""
from datetime import date, timedelta

STAY_STATUSES = ("reserved", "checked_in", "checked_out")
METHODS = ("cash", "card", "upi", "bank", "other")
NUMERIC = ("occupied", "guests", "arrivals", "departures", "new_bookings", "cancellations",
           "events", "room_revenue", "venue_revenue", "extra_revenue", "revenue",
           "invoices", "invoiced", "tax", "collected") + tuple(f"pay_{m}" for m in METHODS)


def _blank(day, total_rooms):
    return {"date": day, "total_rooms": total_rooms, **{k: 0 for k in NUMERIC}}


def _grouped(db, sql, params):
    return {r[0]: r for r in db.execute(sql, params).fetchall()}


def range_report(db, start, end):
    """One row per day in [start, end] plus a totals row.

    Room revenue is accrued per night stayed; venue revenue on the event day;
    extras on the charge date. Invoiced and collected amounts are by issue and
    payment date. All revenue figures are before tax.
    """
    if end < start:
        start, end = end, start
    s, e = start.isoformat(), end.isoformat()
    total_rooms = db.execute("SELECT COUNT(*) FROM rooms WHERE active = 1").fetchone()[0]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows = {d.isoformat(): _blank(d.isoformat(), total_rooms) for d in days}
    marks = ",".join("?" * len(STAY_STATUSES))

    stays = db.execute(
        f"SELECT check_in, check_out, rate, extra_beds, extra_bed_rate, discount, adults, children "
        f"FROM bookings WHERE status IN ({marks}) AND check_in <= ? AND check_out > ?",
        (*STAY_STATUSES, e, s)).fetchall()
    for b in stays:
        ci, co = date.fromisoformat(b["check_in"]), date.fromisoformat(b["check_out"])
        nightly = b["rate"] + b["extra_beds"] * b["extra_bed_rate"] - b["discount"] / (co - ci).days
        d = max(ci, start)
        while d < co and d <= end:
            row = rows[d.isoformat()]
            row["occupied"] += 1
            row["guests"] += b["adults"] + b["children"]
            row["room_revenue"] += nightly
            d += timedelta(days=1)

    counts = [
        ("arrivals", f"SELECT check_in, COUNT(*) FROM bookings WHERE status IN ({marks}) "
                     f"AND check_in BETWEEN ? AND ? GROUP BY check_in", (*STAY_STATUSES, s, e)),
        ("departures", f"SELECT check_out, COUNT(*) FROM bookings WHERE status IN ({marks}) "
                       f"AND check_out BETWEEN ? AND ? GROUP BY check_out", (*STAY_STATUSES, s, e)),
        ("new_bookings", "SELECT d, COUNT(*) FROM (SELECT substr(created_at, 1, 10) d FROM bookings "
                         "UNION ALL SELECT substr(created_at, 1, 10) FROM venue_bookings) "
                         "WHERE d BETWEEN ? AND ? GROUP BY d", (s, e)),
        ("cancellations", "SELECT d, COUNT(*) FROM (SELECT substr(cancelled_at, 1, 10) d FROM bookings "
                          "WHERE status = 'cancelled' UNION ALL SELECT substr(cancelled_at, 1, 10) "
                          "FROM venue_bookings WHERE status = 'cancelled') WHERE d BETWEEN ? AND ? GROUP BY d",
         (s, e)),
    ]
    for key, sql, params in counts:
        for d, r in _grouped(db, sql, params).items():
            rows[d][key] = r[1]

    for d, r in _grouped(db, "SELECT event_date, COUNT(*), SUM(rate - discount) FROM venue_bookings "
                             "WHERE status != 'cancelled' AND event_date BETWEEN ? AND ? GROUP BY event_date",
                         (s, e)).items():
        rows[d]["events"], rows[d]["venue_revenue"] = r[1], r[2] or 0

    # Extras on cancelled / no-show bookings are never billed, so leave them out.
    for d, r in _grouped(db, """
            SELECT c.charge_date, SUM(c.quantity * c.unit_price) FROM charges c
            LEFT JOIN bookings b ON b.id = c.booking_id
            LEFT JOIN venue_bookings v ON v.id = c.venue_booking_id
            WHERE c.charge_date BETWEEN ? AND ?
              AND COALESCE(b.status, v.status) NOT IN ('cancelled', 'no_show')
            GROUP BY c.charge_date""", (s, e)).items():
        rows[d]["extra_revenue"] = r[1] or 0

    for d, r in _grouped(db, "SELECT issue_date, COUNT(*), SUM(total), SUM(tax_total) FROM invoices "
                             "WHERE status = 'issued' AND issue_date BETWEEN ? AND ? GROUP BY issue_date",
                         (s, e)).items():
        rows[d]["invoices"], rows[d]["invoiced"], rows[d]["tax"] = r[1], r[2] or 0, r[3] or 0

    for r in db.execute("SELECT paid_on, method, SUM(amount) FROM payments WHERE paid_on BETWEEN ? AND ? "
                        "GROUP BY paid_on, method", (s, e)).fetchall():
        rows[r[0]][f"pay_{r[1]}"] += r[2]
        rows[r[0]]["collected"] += r[2]

    out = list(rows.values())
    for row in out:
        row["revenue"] = row["room_revenue"] + row["venue_revenue"] + row["extra_revenue"]
        _ratios(row, row["total_rooms"])

    totals = _blank("Total", total_rooms)
    for row in out:
        for k in NUMERIC:
            totals[k] += row[k]
    _ratios(totals, total_rooms * len(out))
    return {"start": s, "end": e, "rows": out, "totals": totals, "total_rooms": total_rooms}


def _ratios(row, room_nights):
    row["occupancy"] = (row["occupied"] / room_nights * 100) if room_nights else 0
    row["adr"] = row["room_revenue"] / row["occupied"] if row["occupied"] else 0
    row["revpar"] = row["room_revenue"] / room_nights if room_nights else 0


def daily_details(db, day):
    """Lists that accompany the daily progress report for one day."""
    d = day.isoformat()
    guest_cols = "b.*, g.full_name AS guest_name, g.phone, r.number AS room_number"
    joins = "FROM bookings b JOIN guests g ON g.id = b.guest_id JOIN rooms r ON r.id = b.room_id"
    return {
        "arrivals": db.execute(f"SELECT {guest_cols} {joins} WHERE b.check_in = ? "
                               "AND b.status IN ('reserved', 'checked_in', 'checked_out') ORDER BY r.number",
                               (d,)).fetchall(),
        "departures": db.execute(f"SELECT {guest_cols} {joins} WHERE b.check_out = ? "
                                 "AND b.status IN ('reserved', 'checked_in', 'checked_out') ORDER BY r.number",
                                 (d,)).fetchall(),
        "in_house": db.execute(f"SELECT {guest_cols} {joins} WHERE b.check_in <= ? AND b.check_out > ? "
                               "AND b.status IN ('checked_in', 'checked_out') ORDER BY r.number",
                               (d, d)).fetchall(),
        "events": db.execute("SELECT vb.*, v.name AS venue_name, g.full_name AS guest_name FROM venue_bookings vb "
                             "JOIN venues v ON v.id = vb.venue_id JOIN guests g ON g.id = vb.guest_id "
                             "WHERE vb.event_date = ? AND vb.status != 'cancelled' ORDER BY v.name",
                             (d,)).fetchall(),
        "payments": db.execute("""
            SELECT p.*, COALESCE(b.code, v.code) AS ref_code, g.full_name AS guest_name
            FROM payments p
            LEFT JOIN bookings b ON b.id = p.booking_id
            LEFT JOIN venue_bookings v ON v.id = p.venue_booking_id
            LEFT JOIN guests g ON g.id = COALESCE(b.guest_id, v.guest_id)
            WHERE p.paid_on = ? ORDER BY p.id""", (d,)).fetchall(),
    }
