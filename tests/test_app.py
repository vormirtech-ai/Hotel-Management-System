"""End-to-end and unit tests. Run:  python -m unittest discover -s tests -v"""
import json
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import billing, create_app, forecast, licensing, review_ai

TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731


def make_keys():
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, pem


def make_license(key, **over):
    payload = {"licensee": "Test Hotel", "edition": "standard", "expires": "never", "install": "*",
               "max_rooms": 0, "issued": TODAY.isoformat(), **over}
    raw = json.dumps(payload, sort_keys=True).encode()
    return licensing.PREFIX + licensing.b64e(raw) + "." + licensing.b64e(key.sign(raw))


class BillingTests(unittest.TestCase):
    TAXES = [
        {"name": "CGST", "rate": 2.5, "applies_to": "room", "min_amount": 0, "max_amount": 7500, "active": 1},
        {"name": "SGST", "rate": 2.5, "applies_to": "room", "min_amount": 0, "max_amount": 7500, "active": 1},
        {"name": "CGST", "rate": 9, "applies_to": "room", "min_amount": 7500.01, "max_amount": None, "active": 1},
        {"name": "SGST", "rate": 9, "applies_to": "room", "min_amount": 7500.01, "max_amount": None, "active": 1},
        {"name": "CESS", "rate": 1, "applies_to": "all", "min_amount": 0, "max_amount": None, "active": 0},
    ]

    def test_room_slab_low_and_high(self):
        low = billing.compute_bill([{"description": "r", "category": "room", "qty": 2, "rate": 5000}], self.TAXES)
        self.assertEqual(low["tax_total"], 500.0)  # 5% of 10,000
        self.assertEqual(low["total"], 10500.0)
        high = billing.compute_bill([{"description": "r", "category": "room", "qty": 1, "rate": 8000}], self.TAXES)
        self.assertEqual(high["tax_total"], 1440.0)  # 18% of 8,000

    def test_discount_moves_line_into_lower_slab(self):
        # 8,000/night less 1,000 discount over 2 nights = 7,500/night -> 5% slab
        bill = billing.compute_bill([{"description": "r", "category": "room", "qty": 2, "rate": 8000,
                                      "discount": 1000}], self.TAXES)
        self.assertEqual(bill["taxable"], 15000.0)
        self.assertEqual(bill["tax_total"], 750.0)

    def test_round_off(self):
        bill = billing.compute_bill([{"description": "x", "category": "room", "qty": 1, "rate": 1000.30}], self.TAXES)
        self.assertEqual(bill["total"], 1050.0)
        self.assertAlmostEqual(bill["round_off"], -0.32, places=2)
        exact = billing.compute_bill([{"description": "x", "category": "room", "qty": 1, "rate": 1000.30}],
                                     self.TAXES, round_off=False)
        self.assertEqual(exact["total"], 1050.32)

    def test_amount_in_words(self):
        self.assertEqual(billing.amount_in_words(0), "Rupees Zero Only")
        self.assertEqual(billing.amount_in_words(1250050.75),
                         "Rupees Twelve Lakh Fifty Thousand Fifty and Seventy Five Paise Only")
        self.assertEqual(billing.number_to_words(110_000_000), "Eleven Crore")
        self.assertEqual(billing.number_to_words(999), "Nine Hundred Ninety Nine")

    def test_late_checkout_fee(self):
        settings = {"late_checkout_fee_percent": "50", "check_out_time": "11:00", "late_checkout_grace_hours": "1"}
        booking = {"check_out": "2026-01-10", "rate": 4000}
        self.assertEqual(billing.late_checkout_fee(settings, booking, datetime(2026, 1, 10, 11, 59)), 0)
        self.assertEqual(billing.late_checkout_fee(settings, booking, datetime(2026, 1, 10, 12, 30)), 2000)
        self.assertEqual(billing.late_checkout_fee({**settings, "late_checkout_fee_percent": "0"}, booking,
                                                   datetime(2026, 1, 10, 18, 0)), 0)


class LicensingTests(unittest.TestCase):
    def setUp(self):
        self.key, self.pem = make_keys()

    def test_valid_and_tampered(self):
        good = make_license(self.key)
        self.assertTrue(licensing.verify_key(good, "ABC", public_key_pem=self.pem).valid)
        body, sig = good[len(licensing.PREFIX):].split(".")
        forged_body = licensing.b64e(json.dumps({"licensee": "Pirate", "expires": "never"}).encode())
        forged = licensing.PREFIX + forged_body + "." + sig
        self.assertFalse(licensing.verify_key(forged, "ABC", public_key_pem=self.pem).valid)
        other_key, _ = make_keys()
        self.assertFalse(licensing.verify_key(make_license(other_key), "ABC", public_key_pem=self.pem).valid)
        self.assertFalse(licensing.verify_key("garbage", "ABC", public_key_pem=self.pem).valid)
        self.assertFalse(licensing.verify_key("HMS1-%%%.%%%", "ABC", public_key_pem=self.pem).valid)

    def test_expiry_and_installation_binding(self):
        expired = make_license(self.key, expires=(TODAY - timedelta(days=1)).isoformat())
        self.assertIn("expired", licensing.verify_key(expired, "A", public_key_pem=self.pem).reason)
        bound = make_license(self.key, install="MACHINE1")
        self.assertTrue(licensing.verify_key(bound, "MACHINE1", public_key_pem=self.pem).valid)
        self.assertFalse(licensing.verify_key(bound, "MACHINE2", public_key_pem=self.pem).valid)

    def test_trial(self):
        self.assertEqual(licensing.trial_days_left(TODAY.isoformat()), licensing.TRIAL_DAYS)
        self.assertEqual(licensing.trial_days_left((TODAY - timedelta(days=30)).isoformat()), 0)
        self.assertEqual(licensing.trial_days_left("bad"), 0)


class ForecastTests(unittest.TestCase):
    def test_weekday_average_respects_on_books(self):
        day = date(2026, 3, 2)  # a Monday
        history = {day - timedelta(weeks=w): 10 for w in range(1, 5)}
        history.update({day - timedelta(days=1): 99})  # Sunday, ignored for Monday
        out = forecast.forecast_rooms(history, {day: 4, day + timedelta(days=7): 12}, [day, day + timedelta(days=7)])
        self.assertEqual(out[day], 10)
        self.assertEqual(out[day + timedelta(days=7)], 12)


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_response(text, finish="STOP", block=None):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
                           prompt_feedback=SimpleNamespace(block_reason=block) if block else None)


class ReviewAITests(unittest.TestCase):
    ARGS = ("Deluxe Room", "Hotel room", ["sea view", "king bed"])

    def run_with(self, **kw):
        client = SimpleNamespace(models=FakeModels(**kw))
        return review_ai.generate_review("key", "gemini-test", *self.ARGS, client=client), client

    def test_success_and_safety_settings(self):
        out, client = self.run_with(response=fake_response('{"headline": "Great stay", "body": "Lovely."}'))
        self.assertEqual(out, {"headline": "Great stay", "body": "Lovely."})
        config = client.models.calls[0]["config"]
        self.assertEqual(len(config.safety_settings), 4)
        self.assertIn("sea view", client.models.calls[0]["contents"])

    def test_failures(self):
        from google.genai import errors
        cases = [
            ({"error": errors.ClientError(429, {"error": {"message": "quota"}})}, 429),
            ({"error": errors.ClientError(403, {"error": {"message": "bad key"}})}, 502),
            ({"error": errors.ServerError(503, {"error": {"message": "down"}})}, 503),
            ({"error": ConnectionError("offline")}, 503),
            ({"response": fake_response("")}, 502),
            ({"response": fake_response(None)}, 502),
            ({"response": fake_response("not json")}, 502),
            ({"response": fake_response('{"headline": "x"}')}, 502),
            ({"response": fake_response(None, finish="SAFETY")}, 422),
            ({"response": fake_response(None, block="SAFETY")}, 422),
        ]
        for kw, status in cases:
            with self.subTest(kw=kw), self.assertRaises(review_ai.ReviewError) as ctx:
                self.run_with(**kw)
            self.assertEqual(ctx.exception.status, status)

    def test_validation(self):
        bad = [None, {}, {"productName": "x", "productCategory": "y", "keyFeatures": []},
               {"productName": "x", "productCategory": "y", "keyFeatures": "fast"},
               {"productName": " ", "productCategory": "y", "keyFeatures": ["a"]},
               {"productName": "x", "productCategory": "y", "keyFeatures": ["a"] * 11}]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(review_ai.ReviewError):
                review_ai.validate_request(body)
        self.assertEqual(review_ai.validate_request({"productName": " A ", "productCategory": "B",
                                                     "keyFeatures": [" x ", "", 5]}), ("A", "B", ["x"]))

    def test_missing_key(self):
        with self.assertRaises(review_ai.ReviewError) as ctx:
            review_ai.generate_review("", "m", *self.ARGS)
        self.assertEqual(ctx.exception.status, 503)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app({"DATA_DIR": Path(self.tmp), "DATABASE": Path(self.tmp) / "t.db",
                               "UPLOAD_DIR": Path(self.tmp) / "uploads"})
        self.c = self.app.test_client()
        r = self.c.post("/welcome", data=self.form("/welcome", hotel_name="Hotel Test", full_name="Admin",
                                                    username="admin", password="secret123", confirm="secret123"))
        self.assertEqual(r.status_code, 302)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def token(self, path="/"):
        page = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def form(self, path="/login", **data):
        return {"csrf_token": self.token(path), **data}

    def post(self, url, **data):
        return self.c.post(url, data={"csrf_token": self.token("/account/password") if self.logged_in() else
                                      self.token("/login"), **data})

    def logged_in(self):
        with self.c.session_transaction() as s:
            return "user_id" in s

    def db(self):
        from app.db import connect
        return connect(self.app.config["DATABASE"])

    def seed_inventory(self):
        self.post("/setup/floors/new", name="Ground", level="0")
        self.post("/setup/room-types/new", name="Deluxe", base_rate="4000", max_occupancy="2", extra_bed_rate="800")
        self.post("/setup/room-types/new", name="Suite", base_rate="9000", max_occupancy="3", extra_bed_rate="1000")
        r = self.post("/setup/rooms/new", number="101", floor_id="1", room_type_id="1", status="available", active="1")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:500])
        self.post("/setup/rooms/new", number="102", floor_id="1", room_type_id="2", status="available", active="1")
        self.post("/setup/venues/new", name="Crystal Hall", kind="banquet", capacity="300", full_day_rate="100000",
                  half_day_rate="60000", active="1")

    def book(self, room="1", ci=D(0), co=D(2), rate="4000", **extra):
        data = {"check_in": ci, "check_out": co, "room_id": room, "adults": "2", "children": "0", "rate": rate,
                "extra_beds": "0", "discount": "0", "source": "walk-in", "guest_mode": "new",
                "g_full_name": "Ravi Kumar", "g_phone": "9999999999", **extra}
        return self.post("/bookings/new", **data)

    def test_csrf_required(self):
        r = self.c.post("/setup/floors/new", data={"name": "X", "level": "1"})
        self.assertEqual(r.status_code, 400)

    def test_login_logout_and_bad_password(self):
        self.post("/logout")
        self.assertFalse(self.logged_in())
        r = self.c.post("/login", data=self.form("/login", username="admin", password="wrong"))
        self.assertIn("Incorrect username or password", r.get_data(as_text=True))
        r = self.c.post("/login?next=//evil.com", data=self.form("/login", username="admin", password="secret123"))
        self.assertEqual(r.location, "/")

    def test_full_stay_flow(self):
        self.seed_inventory()
        r = self.book(advance_amount="2000", advance_method="upi")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:800])
        db = self.db()
        b = db.execute("SELECT * FROM bookings").fetchone()
        self.assertEqual(b["code"], "BK00001")

        # Double booking of the same room is refused; another room is fine.
        r = self.book(ci=D(1), co=D(3))
        self.assertIn("not available", r.get_data(as_text=True))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)
        self.assertEqual(self.book(room="2", ci=D(1), co=D(3), rate="9000").status_code, 302)

        # Over-occupancy rejected.
        r = self.book(room="1", ci=D(5), co=D(6), adults="4")
        self.assertIn("sleeps 2", r.get_data(as_text=True))

        self.assertEqual(self.post(f"/bookings/{b['id']}/check-in").status_code, 302)
        self.assertEqual(db.execute("SELECT status FROM rooms WHERE id = 1").fetchone()[0], "occupied")
        self.post(f"/bookings/{b['id']}/charges", description="Dinner", charge_date=D(0), tax_class="extra",
                  quantity="2", unit_price="500")
        self.post(f"/bookings/{b['id']}/payments", amount="1000", method="cash", paid_on=D(0))

        # Checking out today is an early departure: 1 night billed instead of 2.
        r = self.post(f"/bookings/{b['id']}/check-out")
        self.assertEqual(r.status_code, 302)
        inv = db.execute("SELECT * FROM invoices").fetchone()
        self.assertEqual(inv["number"], "INV-00001")
        # 1 night x 4000 @5% + dinner 1000 @5% = 5250
        self.assertEqual(inv["total"], 5250.0)
        self.assertEqual(db.execute("SELECT status FROM rooms WHERE id = 1").fetchone()[0], "cleaning")
        self.assertEqual(db.execute("SELECT check_out FROM bookings WHERE id = ?", (b["id"],)).fetchone()[0], D(1))
        page = self.c.get(f"/invoices/{inv['id']}").get_data(as_text=True)
        self.assertIn("Rupees Five Thousand Two Hundred Fifty Only", page)
        self.assertIn("₹2,250.00", page)  # balance after 3,000 paid

        # Charges are locked once invoiced; voiding unlocks and re-invoicing gets the next number.
        r = self.post(f"/bookings/{b['id']}/charges", description="Late snack", charge_date=D(0), tax_class="extra",
                      quantity="1", unit_price="100")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM charges").fetchone()[0], 1)
        self.post(f"/invoices/{inv['id']}/void")
        self.post(f"/bookings/{b['id']}/invoice")
        self.assertEqual(db.execute("SELECT number FROM invoices WHERE status = 'issued'").fetchone()[0], "INV-00002")

        # Daily report reflects the day.
        r = self.c.get(f"/reports/daily?date={D(0)}")
        self.assertEqual(r.status_code, 200)
        from app import reports
        row = reports.range_report(db, TODAY, TODAY)["rows"][0]
        self.assertEqual(row["occupied"], 1)  # room 101 tonight (102 starts tomorrow)
        self.assertEqual(row["room_revenue"], 4000)
        self.assertEqual(row["extra_revenue"], 1000)
        self.assertEqual(row["collected"], 3000)
        self.assertEqual(row["pay_upi"], 2000)
        self.assertEqual(self.c.get(f"/reports/range.csv?start={D(0)}&end={D(3)}").status_code, 200)
        db.close()

    def test_events_conflicts(self):
        self.seed_inventory()
        base = {"venue_id": "1", "event_date": D(10), "rate": "100000", "discount": "0", "pax": "200",
                "guest_mode": "new", "g_full_name": "Meera"}
        self.assertEqual(self.post("/events/new", slot="morning", **base).status_code, 302)
        self.assertEqual(self.post("/events/new", slot="evening", **base).status_code, 302)
        r = self.post("/events/new", slot="full", **base)
        self.assertIn("already booked", r.get_data(as_text=True))
        r = self.post("/events/new", slot="morning", **{**base, "pax": "500", "event_date": D(11)})
        self.assertIn("holds up to 300", r.get_data(as_text=True))
        self.post("/events/1/complete")
        db = self.db()
        inv = db.execute("SELECT * FROM invoices").fetchone()
        self.assertEqual(inv["total"], 118000.0)  # 18% GST on banquets
        db.close()

    def test_staff_cannot_open_admin_pages(self):
        self.post("/setup/users/new", username="desk", full_name="Desk", role="staff", active="1", password="deskpass1")
        self.post("/logout")
        self.c.post("/login", data=self.form("/login", username="desk", password="deskpass1"))
        self.assertEqual(self.c.get("/setup/hotel").status_code, 403)
        self.assertEqual(self.c.get("/reports/daily").status_code, 403)
        self.assertEqual(self.c.get("/bookings").status_code, 200)

    def test_trial_expiry_and_activation(self):
        db = self.db()
        db.execute("UPDATE settings SET value = '2000-01-01' WHERE key = 'trial_started'")
        db.commit()
        r = self.c.get("/bookings")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/setup/license", r.location)
        key, pem = make_keys()
        pem_file = Path(self.tmp) / "pub.pem"
        pem_file.write_bytes(pem)
        with mock.patch.object(licensing, "PUBLIC_KEY_FILE", pem_file):
            r = self.post("/setup/license", license_key=make_license(key, max_rooms=1))
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self.c.get("/bookings").status_code, 200)
            # Room limit from the license is enforced.
            self.post("/setup/floors/new", name="G", level="0")
            self.post("/setup/room-types/new", name="Std", base_rate="1000", max_occupancy="2")
            self.post("/setup/rooms/new", number="1", floor_id="1", room_type_id="1", status="available", active="1")
            r = self.post("/setup/rooms/new", number="2", floor_id="1", room_type_id="1", status="available", active="1")
            self.assertIn("license allows 1 rooms", r.get_data(as_text=True))
        db.close()

    def test_review_api(self):
        r = self.c.post("/api/reviews/generate", json={"productName": "x"}, headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/reviews/generate", json={"productName": "x", "productCategory": "y", "keyFeatures": ["a"]},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 503)  # no API key configured
        self.assertIn("API key", r.get_json()["error"])
        fake = {"headline": "H", "body": "B"}
        with mock.patch("app.views.ai.generate_review", return_value=fake):
            r = self.c.post("/api/reviews/generate", headers={"X-CSRF-Token": self.token()},
                            json={"productName": "x", "productCategory": "y", "keyFeatures": ["a"]})
        self.assertEqual(r.get_json(), fake)

    def test_backup_download(self):
        r = self.post("/setup/backup/download")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"SQLite format 3"))


if __name__ == "__main__":
    unittest.main()
