"""Front desk: dashboard, guests, room bookings, events, charges, payments, invoices."""
import json
import sqlite3
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import admin_required, billing, reports
from .. import services as svc
from ..db import get_db, get_settings
from ..forms import Field, parse, section
from ..services import BusinessError

bp = Blueprint("front", __name__)

ID_TYPES = [("", "—"), ("Aadhaar", "Aadhaar"), ("Passport", "Passport"), ("Driving licence", "Driving licence"),
            ("Voter ID", "Voter ID"), ("PAN", "PAN card"), ("Other", "Other")]
SOURCES = [("walk-in", "Walk-in"), ("phone", "Phone"), ("website", "Website"), ("ota", "Online travel agent"),
           ("corporate", "Corporate"), ("agent", "Travel agent")]
EVENT_TYPES = [("Wedding", "Wedding"), ("Reception", "Reception"), ("Engagement", "Engagement"),
               ("Birthday", "Birthday"), ("Conference", "Conference"), ("Corporate", "Corporate event"),
               ("Other", "Other")]
SLOTS = [("full", "Full day"), ("morning", "Morning (half day)"), ("evening", "Evening (half day)")]
METHODS = [("cash", "Cash"), ("card", "Card"), ("upi", "UPI"), ("bank", "Bank transfer"), ("other", "Other")]
TAX_CLASSES = [("extra", "Food, beverages & services"), ("room", "Room-related (e.g. late check-out)"),
               ("venue", "Event-related (e.g. decoration)")]
KINDS = {"bookings": "booking", "events": "event"}

GUEST_FIELDS = [
    Field("full_name", "Full name", required=True, maxlength=120, width="half"),
    Field("phone", "Phone", maxlength=30, width="half"),
    Field("email", "Email", "email", width="half"),
    Field("company", "Company", maxlength=120, width="half"),
    Field("id_type", "ID proof", "select", options=ID_TYPES, width="half"),
    Field("id_number", "ID number", maxlength=40, width="half"),
    Field("gstin", "Guest GSTIN (for business invoices)", maxlength=15, width="half"),
    Field("address", "Address", "textarea"),
]


def _redirect_back(default):
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else default)


# ---------------------------------------------------------------- dashboard

@bp.route("/")
def dashboard():
    db, t = get_db(), date.today()
    rooms = db.execute(svc.ROOM_SQL + " WHERE r.active = 1 ORDER BY f.level, r.number").fetchall()
    in_house = {r["room_id"]: r for r in db.execute(
        "SELECT b.id, b.room_id, b.check_out, g.full_name FROM bookings b JOIN guests g ON g.id = b.guest_id "
        "WHERE b.status = 'checked_in'")}
    floors = {}
    for r in rooms:
        floors.setdefault(r["floor_name"], []).append((r, in_house.get(r["id"])))
    counts = {s: sum(1 for r in rooms if r["status"] == s) for s in ("available", "occupied", "cleaning", "maintenance")}
    today_row = reports.range_report(db, t, t)["rows"][0]
    arrivals = db.execute(
        "SELECT b.*, g.full_name, r.number AS room_number FROM bookings b JOIN guests g ON g.id = b.guest_id "
        "JOIN rooms r ON r.id = b.room_id WHERE b.status = 'reserved' AND b.check_in <= ? ORDER BY b.check_in, r.number",
        (t.isoformat(),)).fetchall()
    departures = db.execute(
        "SELECT b.*, g.full_name, r.number AS room_number FROM bookings b JOIN guests g ON g.id = b.guest_id "
        "JOIN rooms r ON r.id = b.room_id WHERE b.status = 'checked_in' AND b.check_out <= ? ORDER BY r.number",
        (t.isoformat(),)).fetchall()
    events = db.execute(
        "SELECT vb.*, v.name AS venue_name, g.full_name FROM venue_bookings vb JOIN venues v ON v.id = vb.venue_id "
        "JOIN guests g ON g.id = vb.guest_id WHERE vb.status = 'confirmed' AND vb.event_date BETWEEN ? AND ? "
        "ORDER BY vb.event_date, v.name", (t.isoformat(), (t + timedelta(days=7)).isoformat())).fetchall()
    return render_template("dashboard.html", floors=floors, counts=counts, total_rooms=len(rooms),
                           today_row=today_row, arrivals=arrivals, departures=departures, events=events,
                           in_house_count=len(in_house), now_hour=datetime.now().hour)


@bp.route("/rooms/<int:room_id>/status", methods=["POST"])
def room_status(room_id):
    db = get_db()
    new = request.form.get("status")
    room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone() or abort(404)
    if new not in ("available", "cleaning", "maintenance"):
        abort(400, "Unknown room status.")
    if room["status"] == "occupied":
        flash(f"Room {room['number']} is occupied. Check the guest out first.", "error")
    else:
        db.execute("UPDATE rooms SET status = ? WHERE id = ?", (new, room_id))
        db.commit()
        flash(f"Room {room['number']} marked {new}.", "success")
    return _redirect_back(url_for("front.dashboard"))


# ---------------------------------------------------------------- guests

@bp.route("/guests")
def guests():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    rows = get_db().execute(
        "SELECT g.*, (SELECT COUNT(*) FROM bookings b WHERE b.guest_id = g.id) AS stays, "
        "(SELECT COUNT(*) FROM venue_bookings v WHERE v.guest_id = g.id) AS events FROM guests g "
        "WHERE ? = '' OR g.full_name LIKE ? OR g.phone LIKE ? OR g.email LIKE ? OR g.company LIKE ? "
        "ORDER BY g.id DESC LIMIT 500", (q, like, like, like, like)).fetchall()
    return render_template("guests.html", rows=rows, q=q)


@bp.route("/guests/new", methods=["GET", "POST"], defaults={"guest_id": None})
@bp.route("/guests/<int:guest_id>/edit", methods=["GET", "POST"])
def guest_form(guest_id):
    db = get_db()
    row = db.execute("SELECT * FROM guests WHERE id = ?", (guest_id,)).fetchone() if guest_id else None
    if guest_id and row is None:
        abort(404)
    values, errors = (dict(row) if row else {}), {}
    if request.method == "POST":
        values, errors = parse(GUEST_FIELDS, request.form)
        if not errors:
            cols = list(values)
            if row:
                db.execute(f"UPDATE guests SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                           [values[c] for c in cols] + [row["id"]])
                gid = row["id"]
            else:
                gid = db.execute(f"INSERT INTO guests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                 [values[c] for c in cols]).lastrowid
            db.commit()
            flash("Guest saved.", "success")
            return redirect(url_for("front.guest", guest_id=gid))
    return render_template("form.html", title="Edit guest" if row else "New guest", fields=GUEST_FIELDS,
                           values=values, errors=errors, back=url_for("front.guests"))


@bp.route("/guests/<int:guest_id>")
def guest(guest_id):
    db = get_db()
    row = db.execute("SELECT * FROM guests WHERE id = ?", (guest_id,)).fetchone() or abort(404)
    stays = db.execute("SELECT b.*, r.number AS room_number FROM bookings b JOIN rooms r ON r.id = b.room_id "
                       "WHERE b.guest_id = ? ORDER BY b.check_in DESC", (guest_id,)).fetchall()
    events = db.execute("SELECT vb.*, v.name AS venue_name FROM venue_bookings vb JOIN venues v ON v.id = vb.venue_id "
                        "WHERE vb.guest_id = ? ORDER BY vb.event_date DESC", (guest_id,)).fetchall()
    invoices = db.execute("SELECT * FROM invoices WHERE guest_id = ? ORDER BY id DESC", (guest_id,)).fetchall()
    return render_template("guest.html", guest=row, stays=stays, events=events, invoices=invoices)


def _guest_list(db):
    return db.execute("SELECT id, full_name, phone FROM guests ORDER BY full_name LIMIT 2000").fetchall()


def _guest_id_from_form(db, form, errors):
    """Existing guest (guest_id) or a new guest from the inline fields."""
    if form.get("guest_mode") == "existing":
        gid = form.get("guest_id", "")
        if gid.isdigit() and db.execute("SELECT 1 FROM guests WHERE id = ?", (int(gid),)).fetchone():
            return int(gid)
        errors["guest_id"] = "Please choose a guest."
        return None
    values, guest_errors = parse(GUEST_FIELDS, {k[2:]: v for k, v in form.items() if k.startswith("g_")})
    errors.update({"g_" + k: v for k, v in guest_errors.items()})
    if guest_errors:
        return None
    cols = list(values)
    return db.execute(f"INSERT INTO guests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      [values[c] for c in cols]).lastrowid


def _advance(db, kind, record_id, form, errors):
    amount = form.get("advance_amount", "").strip()
    if not amount:
        return
    try:
        value = round(float(amount), 2)
        if not 0 < value < 1e10:
            raise ValueError
    except ValueError:
        errors["advance_amount"] = "Enter a positive amount or leave blank."
        return
    method = form.get("advance_method", "cash")
    if method not in dict(METHODS):
        method = "cash"
    col = "booking_id" if kind == "booking" else "venue_booking_id"
    db.execute(f"INSERT INTO payments ({col}, amount, method, reference, paid_on, notes, created_at) "
               "VALUES (?, ?, ?, ?, ?, 'Advance', ?)",
               (record_id, value, method, form.get("advance_reference", "")[:80], svc.today(), svc.now_text()))


# ---------------------------------------------------------------- room bookings

@bp.route("/bookings")
def bookings():
    status = request.args.get("status", "active")
    q = request.args.get("q", "").strip()
    where, params = [], []
    if status == "active":
        where.append("b.status IN ('reserved', 'checked_in')")
    elif status in ("reserved", "checked_in", "checked_out", "cancelled", "no_show"):
        where.append("b.status = ?")
        params.append(status)
    if q:
        where.append("(g.full_name LIKE ? OR g.phone LIKE ? OR b.code LIKE ? OR r.number LIKE ?)")
        params += [f"%{q}%"] * 4
    rows = get_db().execute(
        "SELECT b.*, g.full_name, g.phone, r.number AS room_number FROM bookings b "
        "JOIN guests g ON g.id = b.guest_id JOIN rooms r ON r.id = b.room_id "
        + ("WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY CASE b.status WHEN 'checked_in' THEN 0 WHEN 'reserved' THEN 1 ELSE 2 END, b.check_in DESC "
          "LIMIT 500", params).fetchall()
    return render_template("bookings.html", rows=rows, status=status, q=q)


def _stay_values(form, errors, room, allow_past=False, original_check_in=None):
    """Validate the stay part of a booking form."""
    out = {}
    try:
        ci, co = svc.validate_stay_dates(form.get("check_in"), form.get("check_out"))
        if not allow_past and ci < date.today() and ci.isoformat() != original_check_in:
            raise BusinessError("Check-in date cannot be in the past.")
        out["check_in"], out["check_out"] = ci.isoformat(), co.isoformat()
    except BusinessError as exc:
        errors["check_in"] = str(exc)
    fields = [
        Field("adults", "Adults", "int", required=True, min=1, max=50),
        Field("children", "Children", "int", min=0, max=50),
        Field("extra_beds", "Extra beds", "int", min=0, max=10),
        Field("rate", "Rate", "float", required=True, min=0, max=10_000_000),
        Field("discount", "Discount", "float", min=0, max=1e10),
        Field("source", "Source", "select", options=SOURCES),
        Field("notes", "Notes", "textarea"),
    ]
    values, field_errors = parse(fields, form)
    errors.update(field_errors)
    out.update(values)
    if room is not None and not field_errors:
        out["extra_bed_rate"] = room["extra_bed_rate"]
        if out["adults"] + out["children"] > room["max_occupancy"] + out["extra_beds"]:
            errors["adults"] = (f"Room {room['number']} sleeps {room['max_occupancy']} "
                                f"(+1 per extra bed). Add extra beds or choose a larger room.")
        if "check_in" in out:
            nights = billing.nights_between(out["check_in"], out["check_out"])
            stay_value = (out["rate"] + out["extra_beds"] * room["extra_bed_rate"]) * nights
            if out["discount"] > stay_value:
                errors["discount"] = "Discount cannot exceed the stay value."
    return out


def _room_for(db, form, errors):
    rid = form.get("room_id", "")
    room = db.execute(svc.ROOM_SQL + " WHERE r.id = ?", (int(rid),)).fetchone() if rid.isdigit() else None
    if room is None:
        errors["room_id"] = "Please choose a room."
    return room


@bp.route("/bookings/new", methods=["GET", "POST"])
def booking_new():
    db = get_db()
    t = date.today()
    src = request.form if request.method == "POST" else request.args
    check_in = src.get("check_in") or t.isoformat()
    default_out = (date.fromisoformat(check_in) + timedelta(days=1)).isoformat() if _is_date(check_in) else ""
    check_out = src.get("check_out") or default_out
    errors, rooms = {}, []
    try:
        svc.validate_stay_dates(check_in, check_out)
        rooms = svc.available_rooms(db, check_in, check_out)
    except BusinessError as exc:
        errors["check_in"] = str(exc)

    if request.method == "POST":
        try:
            svc.begin(db)
            room = _room_for(db, request.form, errors)
            values = _stay_values(request.form, errors, room)
            if room is not None and "check_in" in values:
                try:
                    svc.ensure_room_free(db, room["id"], values["check_in"], values["check_out"])
                except BusinessError as exc:
                    errors["room_id"] = str(exc)
            guest_id = None if errors else _guest_id_from_form(db, request.form, errors)
            if errors:
                db.rollback()
            else:
                cur = db.execute(
                    "INSERT INTO bookings (guest_id, room_id, check_in, check_out, adults, children, rate, extra_beds, "
                    "extra_bed_rate, discount, source, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (guest_id, room["id"], values["check_in"], values["check_out"], values["adults"],
                     values["children"], values["rate"], values["extra_beds"], values["extra_bed_rate"],
                     values["discount"], values["source"] or "walk-in", values["notes"], svc.now_text()))
                bid = cur.lastrowid
                db.execute("UPDATE bookings SET code = ? WHERE id = ?", (f"BK{bid:05d}", bid))
                _advance(db, "booking", bid, request.form, errors)
                if errors:
                    db.rollback()
                else:
                    db.commit()
                    flash(f"Booking BK{bid:05d} created.", "success")
                    return redirect(url_for("front.booking", booking_id=bid))
        except Exception:
            db.rollback()
            raise
        if errors:
            flash("Please correct the highlighted fields.", "error")
    return render_template("booking_new.html", rooms=rooms, check_in=check_in, check_out=check_out, errors=errors,
                           form=request.form, guests=_guest_list(db), guest_fields=GUEST_FIELDS, sources=SOURCES,
                           methods=METHODS, preset_guest=request.args.get("guest_id", ""),
                           preset_room=request.form.get("room_id") or request.args.get("room", ""))


def _is_date(value):
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _load(kind, record_id):
    db = get_db()
    try:
        return svc.get_booking(db, record_id) if kind == "booking" else svc.get_venue_booking(db, record_id)
    except BusinessError:
        abort(404)


@bp.route("/bookings/<int:booking_id>")
def booking(booking_id):
    db, settings = get_db(), get_settings()
    b = _load("booking", booking_id)
    return render_template("booking.html", b=b, **_ledger(db, "booking", b, settings),
                           guest=db.execute("SELECT * FROM guests WHERE id = ?", (b["guest_id"],)).fetchone(),
                           late_fee=billing.late_checkout_fee(settings, _departure(b)) if b["status"] == "checked_in" else 0)


def _departure(b):
    """Booking as it would be billed if the guest left today (early departure or overstay)."""
    actual = max(date.today(), date.fromisoformat(b["check_in"]) + timedelta(days=1)).isoformat()
    nights_before = billing.nights_between(b["check_in"], b["check_out"])
    out = dict(b)
    out["check_out"] = actual
    # Keep the per-night discount when the number of nights changes.
    out["discount"] = round(b["discount"] * billing.nights_between(b["check_in"], actual) / nights_before, 2)
    return out


def _ledger(db, kind, record, settings):
    bill = svc.compute(db, kind, record, settings)
    paid = svc.paid_total(db, kind, record["id"])
    invoice = svc.live_invoice(db, kind, record["id"])
    total = invoice["total"] if invoice else bill["total"]
    return {"bill": bill, "paid": paid, "balance": round(total - paid, 2), "invoice": invoice,
            "charges": svc.charges_for(db, kind, record["id"]), "payments": svc.payments_for(db, kind, record["id"]),
            "methods": METHODS, "tax_classes": TAX_CLASSES,
            "default_tax_class": "extra" if kind == "booking" else "venue"}


@bp.route("/bookings/<int:booking_id>/edit", methods=["GET", "POST"])
def booking_edit(booking_id):
    db = get_db()
    b = _load("booking", booking_id)
    if b["status"] not in svc.OPEN_STAY:
        flash("Only reserved or in-house bookings can be changed.", "error")
        return redirect(url_for("front.booking", booking_id=booking_id))
    errors = {}
    form = request.form if request.method == "POST" else dict(b)
    src = request.form if request.method == "POST" else request.args
    check_in = b["check_in"] if b["status"] == "checked_in" else src.get("check_in") or b["check_in"]
    check_out = src.get("check_out") or b["check_out"]
    rooms = []
    if _is_date(check_in) and _is_date(check_out) and check_out > check_in:
        rooms = svc.available_rooms(db, check_in, check_out, exclude_booking=booking_id)
    if request.method == "POST":
        svc.begin(db)
        data = dict(request.form)
        if b["status"] == "checked_in":
            data["check_in"] = b["check_in"]  # already arrived
        room = _room_for(db, data, errors)
        values = _stay_values(data, errors, room, original_check_in=b["check_in"])
        if b["status"] == "checked_in" and values.get("check_out") and values["check_out"] <= date.today().isoformat():
            errors["check_out"] = "For an in-house guest the departure must be after today. Use Check out to leave now."
        if room is not None and "check_in" in values and not errors:
            try:
                svc.ensure_room_free(db, room["id"], values["check_in"], values["check_out"], exclude_booking=booking_id)
            except BusinessError as exc:
                errors["room_id"] = str(exc)
        if room is not None and b["status"] == "checked_in" and room["id"] != b["room_id"] \
                and room["status"] in ("occupied", "maintenance"):
            errors["room_id"] = f"Room {room['number']} is {room['status']} right now."
        if errors:
            db.rollback()
            flash("Please correct the highlighted fields.", "error")
        else:
            db.execute("UPDATE bookings SET room_id = ?, check_in = ?, check_out = ?, adults = ?, children = ?, "
                       "rate = ?, extra_beds = ?, extra_bed_rate = ?, discount = ?, source = ?, notes = ? WHERE id = ?",
                       (room["id"], values["check_in"], values["check_out"], values["adults"], values["children"],
                        values["rate"], values["extra_beds"], values["extra_bed_rate"], values["discount"],
                        values["source"] or b["source"], values["notes"], booking_id))
            if b["status"] == "checked_in" and room["id"] != b["room_id"]:
                db.execute("UPDATE rooms SET status = 'cleaning' WHERE id = ?", (b["room_id"],))
                db.execute("UPDATE rooms SET status = 'occupied' WHERE id = ?", (room["id"],))
            db.commit()
            flash("Booking updated.", "success")
            return redirect(url_for("front.booking", booking_id=booking_id))
    return render_template("booking_edit.html", b=b, form=form, rooms=rooms, errors=errors, sources=SOURCES,
                           check_in=check_in, check_out=check_out)


@bp.route("/bookings/<int:booking_id>/check-in", methods=["POST"])
def check_in(booking_id):
    db = get_db()
    svc.begin(db)
    b = _load("booking", booking_id)
    t = svc.today()
    problem = None
    if b["status"] != "reserved":
        problem = "Only reserved bookings can be checked in."
    elif b["check_in"] > t:
        problem = f"This booking starts on {b['check_in']}. Edit the dates first for an early arrival."
    elif b["check_out"] <= t:
        problem = "The stay dates have already passed. Edit the dates or mark it as no-show."
    elif b["room_status"] == "occupied":
        problem = f"Room {b['room_number']} is still occupied. Check out the current guest or move this booking."
    elif b["room_status"] == "maintenance":
        problem = f"Room {b['room_number']} is under maintenance. Move this booking to another room."
    if problem:
        db.rollback()
        flash(problem, "error")
    else:
        db.execute("UPDATE bookings SET status = 'checked_in', checked_in_at = ? WHERE id = ?",
                   (svc.now_text(), booking_id))
        db.execute("UPDATE rooms SET status = 'occupied' WHERE id = ?", (b["room_id"],))
        db.commit()
        flash(f"{b['guest_name']} checked in to room {b['room_number']}.", "success")
    return redirect(url_for("front.booking", booking_id=booking_id))


@bp.route("/bookings/<int:booking_id>/check-out", methods=["GET", "POST"])
def check_out(booking_id):
    db, settings = get_db(), get_settings()
    b = _load("booking", booking_id)
    if b["status"] != "checked_in":
        flash("Only in-house guests can be checked out.", "error")
        return redirect(url_for("front.booking", booking_id=booking_id))
    t = date.today()
    # Bill the nights actually stayed: early departure shortens, overstay extends.
    preview = _departure(b)
    late_fee = billing.late_checkout_fee(settings, preview)
    if request.method == "POST":
        svc.begin(db)
        b = _load("booking", booking_id)
        if b["status"] != "checked_in":
            db.rollback()
            return redirect(url_for("front.booking", booking_id=booking_id))
        preview = _departure(b)
        db.execute("UPDATE bookings SET status = 'checked_out', checked_out_at = ?, check_out = ?, discount = ? "
                   "WHERE id = ?", (svc.now_text(), preview["check_out"], preview["discount"], booking_id))
        if late_fee > 0 and request.form.get("apply_late_fee"):
            db.execute("INSERT INTO charges (booking_id, charge_date, description, tax_class, quantity, unit_price) "
                       "VALUES (?, ?, 'Late check-out fee', 'room', 1, ?)", (booking_id, t.isoformat(), float(late_fee)))
        db.execute("UPDATE rooms SET status = 'cleaning' WHERE id = ?", (b["room_id"],))
        try:
            invoice_id = svc.issue_invoice(db, "booking", _load("booking", booking_id), settings)
        except BusinessError:  # already invoiced earlier
            invoice_id = svc.live_invoice(db, "booking", booking_id)["id"]
        db.commit()
        flash(f"{b['guest_name']} checked out. Room {b['room_number']} marked for cleaning.", "success")
        return redirect(url_for("front.invoice", invoice_id=invoice_id))

    bill = billing.compute_bill(
        billing.booking_lines(preview, f"Room {b['room_number']} – {b['type_name']}",
                              svc.charges_for(db, "booking", booking_id), settings),
        svc.taxes(db), settings.get("round_off") == "1")
    paid = svc.paid_total(db, "booking", booking_id)
    return render_template("check_out.html", b=b, bill=bill, paid=paid, balance=round(bill["total"] - paid, 2),
                           late_fee=late_fee, actual_out=preview["check_out"], planned_out=b["check_out"])


@bp.route("/bookings/<int:booking_id>/<any(cancel, 'no-show', invoice):action>", methods=["POST"])
def booking_action(booking_id, action):
    db = get_db()
    svc.begin(db)
    b = _load("booking", booking_id)
    if action == "cancel" and b["status"] == "reserved":
        db.execute("UPDATE bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?", (svc.now_text(), booking_id))
        flash("Booking cancelled.", "success")
    elif action == "no-show" and b["status"] == "reserved" and b["check_in"] <= svc.today():
        db.execute("UPDATE bookings SET status = 'no_show', cancelled_at = ? WHERE id = ?", (svc.now_text(), booking_id))
        flash("Booking marked as no-show.", "success")
    elif action == "invoice" and b["status"] == "checked_out":
        try:
            invoice_id = svc.issue_invoice(db, "booking", b, get_settings())
            db.commit()
            return redirect(url_for("front.invoice", invoice_id=invoice_id))
        except BusinessError as exc:
            flash(str(exc), "error")
    else:
        flash("That action is not possible for this booking right now.", "error")
    db.commit()
    return redirect(url_for("front.booking", booking_id=booking_id))


# ---------------------------------------------------------------- events (banquets / lawns)

def _venues(db):
    return db.execute("SELECT * FROM venues WHERE active = 1 ORDER BY kind, name").fetchall()


def _event_values(db, form, errors, original_date=None):
    fields = [
        Field("venue_id", "Venue", "select", required=True, options=[(v["id"], v["name"]) for v in _venues(db)]),
        Field("event_date", "Event date", "date", required=True),
        Field("slot", "Slot", "select", required=True, options=SLOTS),
        Field("event_name", "Event name", maxlength=120),
        Field("event_type", "Event type", "select", options=EVENT_TYPES),
        Field("pax", "Expected guests", "int", min=0, max=100000),
        Field("rate", "Rate", "float", required=True, min=0, max=1e10),
        Field("discount", "Discount", "float", min=0, max=1e10),
        Field("notes", "Notes", "textarea"),
    ]
    values, field_errors = parse(fields, form)
    errors.update(field_errors)
    if not field_errors:
        values["venue_id"] = int(values["venue_id"])
        if values["event_date"] < svc.today() and values["event_date"] != original_date:
            errors["event_date"] = "Event date cannot be in the past."
        if values["discount"] > values["rate"]:
            errors["discount"] = "Discount cannot exceed the rate."
        venue = db.execute("SELECT * FROM venues WHERE id = ?", (values["venue_id"],)).fetchone()
        if venue["capacity"] and values["pax"] > venue["capacity"]:
            errors["pax"] = f"{venue['name']} holds up to {venue['capacity']} guests."
    return values


@bp.route("/events")
def events():
    status = request.args.get("status", "upcoming")
    q = request.args.get("q", "").strip()
    where, params = [], []
    if status == "upcoming":
        where += ["vb.status = 'confirmed'", "vb.event_date >= ?"]
        params.append(svc.today())
    elif status in ("confirmed", "completed", "cancelled"):
        where.append("vb.status = ?")
        params.append(status)
    if q:
        where.append("(g.full_name LIKE ? OR vb.event_name LIKE ? OR vb.code LIKE ? OR v.name LIKE ?)")
        params += [f"%{q}%"] * 4
    rows = get_db().execute(
        "SELECT vb.*, v.name AS venue_name, v.kind, g.full_name FROM venue_bookings vb "
        "JOIN venues v ON v.id = vb.venue_id JOIN guests g ON g.id = vb.guest_id "
        + ("WHERE " + " AND ".join(where) if where else "")
        + (" ORDER BY vb.event_date" if status == "upcoming" else " ORDER BY vb.event_date DESC") + " LIMIT 500",
        params).fetchall()
    return render_template("events.html", rows=rows, status=status, q=q)


@bp.route("/events/new", methods=["GET", "POST"])
def event_new():
    db = get_db()
    venues = _venues(db)
    if not venues:
        flash("Add a banquet hall or lawn under Setup first.", "error")
        return redirect(url_for("inventory.listing", kind="venues"))
    errors = {}
    if request.method == "POST":
        svc.begin(db)
        values = _event_values(db, request.form, errors)
        if not errors and svc.venue_conflict(db, values["venue_id"], values["event_date"], values["slot"]):
            errors["slot"] = "This venue is already booked for that date and slot."
        guest_id = None if errors else _guest_id_from_form(db, request.form, errors)
        if not errors:
            vid = db.execute(
                "INSERT INTO venue_bookings (guest_id, venue_id, event_date, slot, event_name, event_type, pax, rate, "
                "discount, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (guest_id, values["venue_id"], values["event_date"], values["slot"], values["event_name"],
                 values["event_type"], values["pax"], values["rate"], values["discount"], values["notes"],
                 svc.now_text())).lastrowid
            db.execute("UPDATE venue_bookings SET code = ? WHERE id = ?", (f"EV{vid:05d}", vid))
            _advance(db, "event", vid, request.form, errors)
        if errors:
            db.rollback()
            flash("Please correct the highlighted fields.", "error")
        else:
            db.commit()
            flash(f"Event EV{vid:05d} booked.", "success")
            return redirect(url_for("front.event", event_id=vid))
    form = request.form if request.method == "POST" else {"event_date": request.args.get("date", svc.today()),
                                                         "slot": "full"}
    return render_template("event_form.html", venues=venues, form=form, errors=errors, guests=_guest_list(db),
                           guest_fields=GUEST_FIELDS, slots=SLOTS, event_types=EVENT_TYPES, methods=METHODS,
                           event=None, preset_guest=request.args.get("guest_id", ""))


@bp.route("/events/<int:event_id>")
def event(event_id):
    db = get_db()
    vb = _load("event", event_id)
    return render_template("event.html", vb=vb, **_ledger(db, "event", vb, get_settings()),
                           guest=db.execute("SELECT * FROM guests WHERE id = ?", (vb["guest_id"],)).fetchone())


@bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
def event_edit(event_id):
    db = get_db()
    vb = _load("event", event_id)
    if vb["status"] != "confirmed":
        flash("Only confirmed events can be changed.", "error")
        return redirect(url_for("front.event", event_id=event_id))
    errors = {}
    if request.method == "POST":
        svc.begin(db)
        values = _event_values(db, request.form, errors, original_date=vb["event_date"])
        if not errors and svc.venue_conflict(db, values["venue_id"], values["event_date"], values["slot"],
                                             exclude=event_id):
            errors["slot"] = "This venue is already booked for that date and slot."
        if errors:
            db.rollback()
            flash("Please correct the highlighted fields.", "error")
        else:
            db.execute("UPDATE venue_bookings SET venue_id = ?, event_date = ?, slot = ?, event_name = ?, event_type = ?, "
                       "pax = ?, rate = ?, discount = ?, notes = ? WHERE id = ?",
                       (values["venue_id"], values["event_date"], values["slot"], values["event_name"],
                        values["event_type"], values["pax"], values["rate"], values["discount"], values["notes"],
                        event_id))
            db.commit()
            flash("Event updated.", "success")
            return redirect(url_for("front.event", event_id=event_id))
    return render_template("event_form.html", venues=_venues(db), form=request.form if request.method == "POST" else dict(vb),
                           errors=errors, slots=SLOTS, event_types=EVENT_TYPES, event=vb)


@bp.route("/events/<int:event_id>/<any(cancel, complete, invoice):action>", methods=["POST"])
def event_action(event_id, action):
    db = get_db()
    svc.begin(db)
    vb = _load("event", event_id)
    if action == "cancel" and vb["status"] == "confirmed":
        db.execute("UPDATE venue_bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
                   (svc.now_text(), event_id))
        flash("Event cancelled.", "success")
    elif action in ("complete", "invoice") and vb["status"] in ("confirmed", "completed"):
        db.execute("UPDATE venue_bookings SET status = 'completed' WHERE id = ?", (event_id,))
        try:
            invoice_id = svc.issue_invoice(db, "event", _load("event", event_id), get_settings())
            db.commit()
            flash("Event completed and invoiced.", "success")
            return redirect(url_for("front.invoice", invoice_id=invoice_id))
        except BusinessError as exc:
            flash(str(exc), "error")
    else:
        flash("That action is not possible for this event right now.", "error")
    db.commit()
    return redirect(url_for("front.event", event_id=event_id))


# ---------------------------------------------------------------- charges & payments

def _target(segment, record_id):
    kind = KINDS.get(segment) or abort(404)
    record = _load(kind, record_id)
    detail = url_for("front.booking", booking_id=record_id) if kind == "booking" else url_for("front.event",
                                                                                                event_id=record_id)
    return kind, record, detail


@bp.route("/<segment>/<int:record_id>/charges", methods=["POST"])
def add_charge(segment, record_id):
    db = get_db()
    kind, record, detail = _target(segment, record_id)
    fields = [
        Field("description", "Description", required=True, maxlength=120),
        Field("charge_date", "Date", "date", required=True),
        Field("tax_class", "Type", "select", required=True, options=TAX_CLASSES),
        Field("quantity", "Qty", "float", required=True, min=0.01, max=100000),
        Field("unit_price", "Price", "float", required=True, min=0, max=1e10),
    ]
    values, errors = parse(fields, request.form)
    if record["status"] in ("cancelled", "no_show") or svc.live_invoice(db, kind, record_id):
        errors["status"] = "Charges cannot be added after invoicing or cancellation."
    if errors:
        flash("Charge not added: " + "; ".join(sorted(set(errors.values()))), "error")
    else:
        col = "booking_id" if kind == "booking" else "venue_booking_id"
        db.execute(f"INSERT INTO charges ({col}, charge_date, description, tax_class, quantity, unit_price, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (record_id, values["charge_date"], values["description"],
                                                    values["tax_class"], values["quantity"], values["unit_price"],
                                                    svc.now_text()))
        db.commit()
        flash("Charge added.", "success")
    return redirect(detail + "#charges")


@bp.route("/charges/<int:charge_id>/delete", methods=["POST"])
def delete_charge(charge_id):
    db = get_db()
    c = db.execute("SELECT * FROM charges WHERE id = ?", (charge_id,)).fetchone() or abort(404)
    kind, rid = ("booking", c["booking_id"]) if c["booking_id"] else ("event", c["venue_booking_id"])
    if svc.live_invoice(db, kind, rid):
        flash("This charge is on an issued invoice. Void the invoice first.", "error")
    else:
        db.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
        db.commit()
        flash("Charge removed.", "success")
    return _redirect_back(url_for("front.dashboard"))


@bp.route("/<segment>/<int:record_id>/payments", methods=["POST"])
def add_payment(segment, record_id):
    db = get_db()
    kind, record, detail = _target(segment, record_id)
    fields = [
        Field("amount", "Amount", "float", required=True, min=-1e10, max=1e10),
        Field("method", "Method", "select", required=True, options=METHODS),
        Field("reference", "Reference", maxlength=80),
        Field("paid_on", "Date", "date", required=True),
        Field("notes", "Notes", maxlength=120),
    ]
    values, errors = parse(fields, request.form)
    if not errors and values["amount"] == 0:
        errors["amount"] = "Amount cannot be zero."
    if not errors and values["paid_on"] > svc.today():
        errors["paid_on"] = "Payment date cannot be in the future."
    if errors:
        flash("Payment not recorded: " + "; ".join(sorted(set(errors.values()))), "error")
    else:
        col = "booking_id" if kind == "booking" else "venue_booking_id"
        db.execute(f"INSERT INTO payments ({col}, amount, method, reference, paid_on, notes, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (record_id, values["amount"], values["method"], values["reference"],
                                                    values["paid_on"], values["notes"], svc.now_text()))
        db.commit()
        flash("Refund recorded." if values["amount"] < 0 else "Payment recorded.", "success")
    return redirect(detail + "#payments")


@bp.route("/payments/<int:payment_id>/delete", methods=["POST"])
@admin_required
def delete_payment(payment_id):
    db = get_db()
    db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    db.commit()
    flash("Payment deleted.", "success")
    return _redirect_back(url_for("front.dashboard"))


# ---------------------------------------------------------------- invoices

@bp.route("/invoices")
def invoices():
    q = request.args.get("q", "").strip()
    start, end = request.args.get("start", ""), request.args.get("end", "")
    where, params = ["1 = 1"], []
    if q:
        where.append("(i.number LIKE ? OR g.full_name LIKE ?)")
        params += [f"%{q}%"] * 2
    if _is_date(start):
        where.append("i.issue_date >= ?")
        params.append(start)
    if _is_date(end):
        where.append("i.issue_date <= ?")
        params.append(end)
    rows = get_db().execute(
        "SELECT i.*, g.full_name FROM invoices i JOIN guests g ON g.id = i.guest_id WHERE "
        + " AND ".join(where) + " ORDER BY i.id DESC LIMIT 500", params).fetchall()
    return render_template("invoices.html", rows=rows, q=q, start=start, end=end)


@bp.route("/invoices/<int:invoice_id>")
def invoice(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone() or abort(404)
    kind, rid = ("booking", inv["booking_id"]) if inv["booking_id"] else ("event", inv["venue_booking_id"])
    record = _load(kind, rid)
    paid = svc.paid_total(db, kind, rid)
    return render_template("invoice.html", inv=inv, lines=json.loads(inv["lines_json"]),
                           tax_summary=json.loads(inv["taxes_json"]), hotel=json.loads(inv["hotel_json"]),
                           guest=json.loads(inv["guest_json"]), record=record, kind=kind, paid=paid,
                           balance=round(inv["total"] - paid, 2), words=billing.amount_in_words(inv["total"]),
                           payments=svc.payments_for(db, kind, rid))


@bp.route("/invoices/<int:invoice_id>/void", methods=["POST"])
@admin_required
def void_invoice(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone() or abort(404)
    db.execute("UPDATE invoices SET status = 'void' WHERE id = ?", (invoice_id,))
    db.commit()
    flash(f"Invoice {inv['number']} voided. You can now correct the booking and issue a new invoice.", "success")
    if inv["booking_id"]:
        return redirect(url_for("front.booking", booking_id=inv["booking_id"]))
    return redirect(url_for("front.event", event_id=inv["venue_booking_id"]))
