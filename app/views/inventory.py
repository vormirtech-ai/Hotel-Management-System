"""Setup of floors, room types, rooms, banquet halls / lawns and tax rules."""
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import admin_required, license_state
from ..db import get_db
from ..forms import Field, parse

bp = Blueprint("inventory", __name__, url_prefix="/setup")

ROOM_STATUS = [("available", "Available"), ("cleaning", "Cleaning"), ("maintenance", "Maintenance"),
               ("occupied", "Occupied")]


def _floor_options():
    return [(r["id"], f"{r['name']} (level {r['level']})")
            for r in get_db().execute("SELECT * FROM floors ORDER BY level")]


def _type_options():
    return [(r["id"], r["name"]) for r in get_db().execute("SELECT * FROM room_types ORDER BY name")]


# Each entity: table, titles, list query + columns, and form fields.
# Column formats: money, num, bool, status, pct, or None for plain text.
ENTITIES = {
    "floors": {
        "title": "Floors", "singular": "floor", "table": "floors",
        "intro": "Floors group your rooms on the dashboard and in reports.",
        "list_sql": "SELECT f.*, (SELECT COUNT(*) FROM rooms r WHERE r.floor_id = f.id) AS rooms "
                    "FROM floors f ORDER BY f.level",
        "columns": [("level", "Level", None), ("name", "Name", None), ("rooms", "Rooms", None), ("notes", "Notes", None)],
        "fields": lambda: [
            Field("name", "Floor name", required=True, placeholder="e.g. Ground Floor", width="half"),
            Field("level", "Level number", "int", required=True, help="0 for ground, negative for basements.",
                  min=-10, max=200, width="half"),
            Field("notes", "Notes", "textarea"),
        ],
    },
    "room-types": {
        "title": "Room types", "singular": "room type", "table": "room_types",
        "intro": "Categories of rooms with their standard tariff.",
        "list_sql": "SELECT t.*, (SELECT COUNT(*) FROM rooms r WHERE r.room_type_id = t.id) AS rooms "
                    "FROM room_types t ORDER BY t.base_rate",
        "columns": [("name", "Name", None), ("base_rate", "Tariff / night", "money"),
                    ("max_occupancy", "Max guests", None), ("extra_bed_rate", "Extra bed", "money"),
                    ("rooms", "Rooms", None)],
        "fields": lambda: [
            Field("name", "Name", required=True, placeholder="e.g. Deluxe King", width="half"),
            Field("base_rate", "Tariff per night", "float", required=True, min=0, max=10_000_000, width="half"),
            Field("max_occupancy", "Max guests", "int", required=True, min=1, max=50, width="half"),
            Field("extra_bed_rate", "Extra bed per night", "float", min=0, max=10_000_000, width="half"),
            Field("amenities", "Amenities", placeholder="AC, Wi-Fi, TV, Mini bar", maxlength=300),
            Field("description", "Description", "textarea"),
        ],
    },
    "rooms": {
        "title": "Rooms", "singular": "room", "table": "rooms",
        "intro": "Every bookable room. Leave the tariff blank to use the room type's tariff.",
        "list_sql": "SELECT r.*, f.name AS floor, t.name AS type, COALESCE(r.rate, t.base_rate) AS tariff "
                    "FROM rooms r JOIN floors f ON f.id = r.floor_id JOIN room_types t ON t.id = r.room_type_id "
                    "ORDER BY f.level, r.number",
        "columns": [("number", "Room", None), ("floor", "Floor", None), ("type", "Type", None),
                    ("tariff", "Tariff / night", "money"), ("status", "Status", "status"), ("active", "Active", "bool")],
        "fields": lambda: [
            Field("number", "Room number", required=True, maxlength=20, width="half"),
            Field("floor_id", "Floor", "select", required=True, options=_floor_options(), width="half"),
            Field("room_type_id", "Room type", "select", required=True, options=_type_options(), width="half"),
            Field("rate", "Tariff override", "float", min=0, max=10_000_000, blank_none=True,
                  help="Optional. Blank = room type tariff.", width="half"),
            Field("status", "Housekeeping status", "select", required=True, options=ROOM_STATUS[:3], width="half"),
            Field("active", "Active (bookable)", "bool", width="half"),
            Field("notes", "Notes", "textarea"),
        ],
    },
    "venues": {
        "title": "Banquet halls & lawns", "singular": "venue", "table": "venues",
        "intro": "Event spaces that can be booked for full or half days.",
        "list_sql": "SELECT * FROM venues ORDER BY kind, name",
        "columns": [("name", "Name", None), ("kind", "Type", "kind"), ("capacity", "Capacity", None),
                    ("full_day_rate", "Full day", "money"), ("half_day_rate", "Half day", "money"),
                    ("active", "Active", "bool")],
        "fields": lambda: [
            Field("name", "Name", required=True, placeholder="e.g. Crystal Banquet", width="half"),
            Field("kind", "Type", "select", required=True, options=[("banquet", "Banquet hall"), ("lawn", "Lawn")],
                  width="half"),
            Field("capacity", "Capacity (guests)", "int", min=0, max=100000, width="half"),
            Field("area_sqft", "Area (sq. ft.)", "int", min=0, max=10_000_000, width="half"),
            Field("full_day_rate", "Full day rate", "float", required=True, min=0, max=100_000_000, width="half"),
            Field("half_day_rate", "Half day rate (morning / evening)", "float", required=True, min=0,
                  max=100_000_000, width="half"),
            Field("active", "Active (bookable)", "bool"),
            Field("description", "Description", "textarea"),
        ],
    },
    "taxes": {
        "title": "Taxes", "singular": "tax rule", "table": "taxes",
        "intro": "Tax rules are applied automatically on invoices. A rule applies when the bill line's category "
                 "matches and its per-unit rate (e.g. room tariff per night) falls inside the slab. "
                 "Defaults follow Indian GST slabs – please confirm them with your tax advisor.",
        "list_sql": "SELECT * FROM taxes ORDER BY applies_to, min_amount, name",
        "columns": [("name", "Tax", None), ("rate", "Rate", "pct"), ("applies_to", "Applies to", "kind"),
                    ("min_amount", "Slab from", "money"), ("max_amount", "Slab up to", "money_open"),
                    ("active", "Active", "bool")],
        "fields": lambda: [
            Field("name", "Tax name", required=True, placeholder="e.g. CGST", maxlength=40, width="half"),
            Field("rate", "Rate (%)", "float", required=True, min=0, max=100, width="half"),
            Field("applies_to", "Applies to", "select", required=True,
                  options=[("room", "Room tariff"), ("venue", "Banquet / lawn"), ("extra", "Extras (F&B, services)"),
                           ("all", "Everything")], width="half"),
            Field("active", "Active", "bool", width="half"),
            Field("min_amount", "Slab from (per unit)", "float", min=0, width="half"),
            Field("max_amount", "Slab up to (per unit)", "float", min=0, blank_none=True,
                  help="Blank = no upper limit.", width="half"),
        ],
    },
}

DEFAULTS = {"rooms": {"status": "available", "active": 1}, "venues": {"active": 1, "kind": "banquet"},
            "taxes": {"active": 1, "applies_to": "room"}, "room-types": {"max_occupancy": 2}}


def _entity(kind):
    return ENTITIES.get(kind) or abort(404)


@bp.route("/<kind>")
@admin_required
def listing(kind):
    ent = _entity(kind)
    rows = get_db().execute(ent["list_sql"]).fetchall()
    return render_template("crud_list.html", kind=kind, ent=ent, rows=rows)


@bp.route("/<kind>/new", methods=["GET", "POST"], defaults={"item_id": None})
@bp.route("/<kind>/<int:item_id>", methods=["GET", "POST"])
@admin_required
def edit(kind, item_id):
    ent, db = _entity(kind), get_db()
    row = None
    if item_id:
        row = db.execute(f"SELECT * FROM {ent['table']} WHERE id = ?", (item_id,)).fetchone() or abort(404)
    fields = ent["fields"]()
    if kind == "rooms" and row and row["status"] == "occupied":
        # Occupied is set by check-in / check-out only.
        fields = [f for f in fields if f.name != "status"]
    values, errors = (dict(row) if row else dict(DEFAULTS.get(kind, {}))), {}
    if kind == "rooms":
        missing = "floors" if not _floor_options() else "room-types" if not _type_options() else None
        if missing:
            flash("Add at least one floor and one room type before adding rooms.", "error")
            return redirect(url_for("inventory.listing", kind=missing))
    if request.method == "POST":
        values, errors = parse(fields, request.form)
        if kind == "taxes" and values.get("max_amount") is not None and values["max_amount"] < values.get("min_amount", 0):
            errors["max_amount"] = "Must be greater than 'slab from'."
        if kind == "rooms" and row is None and not errors:
            errors.update(_room_limit_error())
        if not errors:
            cols = list(values)
            try:
                if row:
                    db.execute(f"UPDATE {ent['table']} SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                               [values[c] for c in cols] + [row["id"]])
                else:
                    db.execute(f"INSERT INTO {ent['table']} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [values[c] for c in cols])
                db.commit()
                flash(f"{ent['singular'].capitalize()} saved.", "success")
                return redirect(url_for("inventory.listing", kind=kind))
            except sqlite3.IntegrityError:
                db.rollback()
                errors[fields[0].name] = "This name / number already exists."
    return render_template("form.html", title=f"{'Edit' if row else 'New'} {ent['singular']}", fields=fields,
                           values=values, errors=errors, back=url_for("inventory.listing", kind=kind),
                           delete_url=url_for("inventory.delete", kind=kind, item_id=row["id"]) if row else None)


def _room_limit_error():
    status, _ = license_state()
    limit = int(status.payload.get("max_rooms", 0) or 0) if status.valid else 0
    count = get_db().execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    if limit and count >= limit:
        return {"number": f"Your license allows {limit} rooms. Contact your vendor to upgrade."}
    return {}


@bp.route("/<kind>/<int:item_id>/delete", methods=["POST"])
@admin_required
def delete(kind, item_id):
    ent, db = _entity(kind), get_db()
    try:
        db.execute(f"DELETE FROM {ent['table']} WHERE id = ?", (item_id,))
        db.commit()
        flash(f"{ent['singular'].capitalize()} deleted.", "success")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"This {ent['singular']} is in use by bookings or rooms. Mark it inactive instead.", "error")
        return redirect(url_for("inventory.edit", kind=kind, item_id=item_id))
    return redirect(url_for("inventory.listing", kind=kind))
