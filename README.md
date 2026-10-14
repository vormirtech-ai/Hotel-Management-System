# Hotel Management System

A complete hotel management application in Python with a clean web interface. All data is stored **locally** in a single SQLite file, so no database server is needed.

Built by **Vormir Tech Solutions**.

## Features

| Area | What you get |
|------|--------------|
| **Setup** | Hotel name, logo, address, GSTIN, PAN, state code, currency. Billing rules: check-in/out times, late check-out fee and grace period, invoice prefix and numbering, round-off, SAC codes, invoice footer. |
| **Taxes** | Tax rules by category (room / banquet & lawn / extras / everything) with tariff slabs, for example the GST rule "≤ ₹7,500 per night at 5%, above that at 18%". Ships with editable Indian GST defaults. |
| **Inventory** | Floors, room types (tariff, occupancy, extra bed, amenities), rooms (tariff override, housekeeping status), **banquet halls and lawns** (capacity, full-day and half-day rates). |
| **Front desk** | Dashboard with a live room grid by floor, arrivals, departures and events. Room bookings with availability search and double-booking protection, check-in, edit, extend or move rooms, early departure and overstay billing, cancellations and no-shows. Event bookings by full day, morning or evening slot with conflict checks. Extra charges, payments, refunds and advances. |
| **Billing** | GST tax invoices with a snapshot of hotel and guest details, a per-line tax breakdown, a tax summary, round-off and the amount in words (Indian system). They print cleanly or save as PDF. Admins can void an invoice and re-issue it. |
| **Reports** | **Daily progress report**: occupancy, ADR, RevPAR, revenue by source, collections by payment method, arrivals, departures, in-house guests, events and payments. **Date range report** for any period, with a chart, per-day table and CSV export. |
| **Licensing** | Digitally signed (Ed25519) license keys with expiry, room limit and optional lock to one installation. Includes a 14-day trial. |
| **Predictive analytics** | 30-day demand forecast from bookings already made and weekday patterns. The model sits in `app/forecast.py` behind one function, so it can be swapped for a better one. |
| **AI review writer** | Generates balanced product reviews (`headline` + `body`) with Google Gemini. Includes safety filters, input validation and graceful errors for rate limits, empty or blocked responses. Also available as a JSON API. |
| **Users** | Administrator and front-desk roles, hashed passwords, CSRF protection and a database backup download. |

## Quick start

Requires **Python 3.10 or newer**.

**Windows:** double-click `start_windows.bat`.

**macOS / Linux:**
```bash
./start.sh
```

**Manual:**
```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
source .venv/bin/activate      # macOS / Linux
pip install -r requirements.txt
python run.py
```

Open **http://localhost:8000**. On first launch you create the administrator account. Then go to **Setup** and add, in this order: hotel profile, floors, room types, rooms, then banquets & lawns.

### Settings (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `HMS_HOST` | `127.0.0.1` | Use `0.0.0.0` to let other computers on the LAN connect |
| `HMS_PORT` | `8000` | Port |
| `HMS_DATA_DIR` | `./instance` | Folder for the database, uploads, secret key and installation ID |
| `HMS_SECRET_KEY` | auto-generated | Session signing key |

`python run.py` starts the production server (Waitress). `python run.py --dev` starts the Flask development server with auto-reload.

### Docker

```bash
docker build -t hotel-ms .
docker run -d -p 8000:8000 -v hotel-data:/data --name hotel hotel-ms
```

## Data and backups

All data lives in `instance/hotel.db` (or `HMS_DATA_DIR`). To back it up, go to **Setup → Backup → Download backup**. This makes a consistent copy even while the app is running. To restore:
1. Stop the app.
2. Delete `hotel.db-wal` and `hotel.db-shm` if they exist.
3. Put the backup file in place as `hotel.db`.
4. Start the app again.

## Licensing

- A new installation runs as a **14-day trial**. After that, only the License page opens until a key is activated.
- **Setup → License** shows the *Installation ID*. A key can be locked to that ID.
- Vendor side (keep the private key secret and never ship it):

```bash
python tools/license_tool.py keygen       # once: creates vendor_keys/license_private_key.pem and updates app/license_public_key.pem
python tools/license_tool.py issue --licensee "Hotel Sunrise" --expires 2027-12-31 --install 3F2A9C1B7D4E8A60 --max-rooms 50
python tools/license_tool.py verify HMS1-... --install 3F2A9C1B7D4E8A60
```

Running `keygen` again rotates the key pair: keys issued before stop working, and the new `app/license_public_key.pem` must ship with the app.

## AI review writer (Gemini)

1. Get an API key at https://aistudio.google.com.
2. Enter it under **Setup → AI settings**. It is stored only in your local database.
3. Open **AI tools → Review writer**. Fill in a name, a category and key features, or start from a room type or venue.

The original brief named `gemini-1.5-flash`. Google has retired that model, so the default is `gemini-2.5-flash`. You can change the model in AI settings.

JSON API (signed-in session plus `X-CSRF-Token` header):
```
POST /api/reviews/generate
{"productName": "Deluxe King Room", "productCategory": "Hotel room", "keyFeatures": ["sea view", "king bed"]}
→ 200 {"headline": "...", "body": "..."}
→ 400 invalid input · 422 blocked by safety filter · 429 rate limit · 502/503 Gemini or network problem: {"error": "..."}
```

## How the reports count

- **Rooms sold / occupancy:** bookings that are reserved, in house or checked out, counted for each night stayed.
- **Room revenue:** the nightly rate plus extra beds, minus the discount spread over the nights. It is counted per night.
- **Event revenue:** the rate minus the discount, on the event date. **Extras:** counted on the charge date.
- **Invoiced:** by issue date. **Collected:** by payment date. Revenue figures are before tax.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover tax slabs, discounts and round-off, amount in words, and late check-out fees. They also cover license signatures, expiry, installation lock and trial, and the forecast. The end-to-end flow (booking, double-booking refusal, check-in, charges, payments, early check-out, invoice, void and re-issue, reports) is tested too. So are event slot conflicts, staff permissions, CSRF, backup, and Gemini error handling with a fake client.

## Project layout

```
app/
  __init__.py        app factory, auth/CSRF/license gate, template filters
  schema.sql, db.py  SQLite schema, settings
  billing.py         tax and bill calculation (pure functions)
  services.py        availability, invoicing (database logic)
  reports.py         daily / range reports
  forecast.py        demand forecast (extension point)
  licensing.py       license key verification
  review_ai.py       Gemini review generator
  views/             auth, front desk, inventory, setup, reports, AI
  templates/, static/
tools/license_tool.py  vendor key generator
tests/                 unittest suite
.claude/skills/        ponytail skills for Claude Code
```

## Claude Code skills (ponytail)

The [ponytail](https://github.com/DietrichGebert/ponytail) skills (MIT) are installed at project level in `.claude/skills/`. Opening this repository in Claude Code makes these commands available: `/ponytail`, `/ponytail-review`, `/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain` and `/ponytail-help`.

To install the full plugin with its lifecycle hooks instead (needs Node.js), run these as two separate prompts:
```
/plugin marketplace add DietrichGebert/ponytail
/plugin install ponytail@ponytail
```
