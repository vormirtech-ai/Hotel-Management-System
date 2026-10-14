"""SQLite access: one connection per request, schema setup and settings."""
import sqlite3
from datetime import date
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")

DEFAULT_SETTINGS = {
    # Hotel profile
    "hotel_name": "My Hotel",
    "hotel_tagline": "",
    "hotel_address": "",
    "hotel_city": "",
    "hotel_state": "",
    "hotel_pincode": "",
    "hotel_phone": "",
    "hotel_email": "",
    "hotel_website": "",
    "hotel_gstin": "",
    "hotel_state_code": "",
    "hotel_pan": "",
    "hotel_logo": "",
    "currency_symbol": "₹",
    # Billing rules
    "check_in_time": "12:00",
    "check_out_time": "11:00",
    "late_checkout_grace_hours": "1",
    "late_checkout_fee_percent": "0",
    "invoice_prefix": "INV-",
    "invoice_next_number": "1",
    "round_off": "1",
    "sac_room": "996311",
    "sac_venue": "",
    "sac_extra": "",
    "invoice_terms": "Thank you for choosing us. We look forward to welcoming you again.",
    # Licensing
    "license_key": "",
    "trial_started": "",
    # AI review writer
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
}

# GST slabs as notified for India (accommodation <= 7,500/night at 5%, above at
# 18%; banquets and F&B as commonly applied). Admins can edit all of these.
DEFAULT_TAXES = [
    ("CGST", 2.5, "room", 0, 7500),
    ("SGST", 2.5, "room", 0, 7500),
    ("CGST", 9, "room", 7500.01, None),
    ("SGST", 9, "room", 7500.01, None),
    ("CGST", 9, "venue", 0, None),
    ("SGST", 9, "venue", 0, None),
    ("CGST", 2.5, "extra", 0, None),
    ("SGST", 2.5, "extra", 0, None),
]


def connect(path):
    conn = sqlite3.connect(path, detect_types=0, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(path):
    """Create tables and seed defaults on first run. Idempotent."""
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            DEFAULT_SETTINGS.items(),
        )
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'trial_started' AND value = ''",
            (date.today().isoformat(),),
        )
        if not conn.execute("SELECT 1 FROM taxes LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO taxes (name, rate, applies_to, min_amount, max_amount) VALUES (?, ?, ?, ?, ?)",
                DEFAULT_TAXES,
            )
        conn.commit()
    finally:
        conn.close()


def get_settings():
    if "settings" not in g:
        rows = get_db().execute("SELECT key, value FROM settings").fetchall()
        g.settings = {**DEFAULT_SETTINGS, **{r["key"]: r["value"] for r in rows}}
    return g.settings


def save_settings(values):
    db = get_db()
    db.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(k, str(v)) for k, v in values.items()],
    )
    db.commit()
    g.pop("settings", None)
