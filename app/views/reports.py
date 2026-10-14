"""Daily progress report, date-range report (with CSV) and demand forecast."""
import csv
import io
from datetime import date, timedelta

from flask import Blueprint, Response, render_template, request

from .. import admin_required, forecast
from .. import reports as rep
from ..db import get_db

bp = Blueprint("reports", __name__, url_prefix="/reports")

MAX_RANGE_DAYS = 731


def _date_arg(name, default):
    try:
        return date.fromisoformat(request.args.get(name, ""))
    except ValueError:
        return default


@bp.route("/daily")
@admin_required
def daily():
    day = _date_arg("date", date.today())
    db = get_db()
    data = rep.range_report(db, day, day)
    return render_template("report_daily.html", day=day, row=data["rows"][0], **rep.daily_details(db, day),
                           prev=day - timedelta(days=1), next=day + timedelta(days=1))


def _range():
    t = date.today()
    start = _date_arg("start", t.replace(day=1))
    end = _date_arg("end", t)
    if end < start:
        start, end = end, start
    if (end - start).days >= MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
    return start, end


@bp.route("/range")
@admin_required
def range_view():
    start, end = _range()
    data = rep.range_report(get_db(), start, end)
    peak = max([r["revenue"] for r in data["rows"]] + [1])
    return render_template("report_range.html", data=data, start=start, end=end, peak=peak,
                           max_days=MAX_RANGE_DAYS, presets=_presets())


def _presets():
    t = date.today()
    first = t.replace(day=1)
    last_month_end = first - timedelta(days=1)
    return [
        ("Last 7 days", t - timedelta(days=6), t),
        ("This month", first, t),
        ("Last month", last_month_end.replace(day=1), last_month_end),
        ("Last 30 days", t - timedelta(days=29), t),
        ("This year", t.replace(month=1, day=1), t),
    ]


CSV_COLUMNS = [
    ("date", "Date"), ("total_rooms", "Rooms"), ("occupied", "Rooms sold"), ("occupancy", "Occupancy %"),
    ("adr", "ADR"), ("revpar", "RevPAR"), ("guests", "Guests"), ("arrivals", "Arrivals"),
    ("departures", "Departures"), ("new_bookings", "New bookings"), ("cancellations", "Cancellations"),
    ("events", "Events"), ("room_revenue", "Room revenue"), ("venue_revenue", "Venue revenue"),
    ("extra_revenue", "Extras revenue"), ("revenue", "Total revenue (pre-tax)"), ("invoices", "Invoices"),
    ("invoiced", "Invoiced (incl. tax)"), ("tax", "Tax"), ("pay_cash", "Cash"), ("pay_card", "Card"),
    ("pay_upi", "UPI"), ("pay_bank", "Bank"), ("pay_other", "Other"), ("collected", "Collected"),
]


@bp.route("/range.csv")
@admin_required
def range_csv():
    start, end = _range()
    data = rep.range_report(get_db(), start, end)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in CSV_COLUMNS])
    for row in data["rows"] + [data["totals"]]:
        writer.writerow([round(row[k], 2) if isinstance(row[k], float) else row[k] for k, _ in CSV_COLUMNS])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=report_{start}_{end}.csv"})


@bp.route("/forecast")
@admin_required
def forecast_view():
    db, t = get_db(), date.today()
    horizon = 30
    history_rows = rep.range_report(db, t - timedelta(weeks=forecast.HISTORY_WEEKS), t - timedelta(days=1))["rows"]
    future = rep.range_report(db, t, t + timedelta(days=horizon - 1))
    history = {date.fromisoformat(r["date"]): r["occupied"] for r in history_rows}
    on_books = {date.fromisoformat(r["date"]): r["occupied"] for r in future["rows"]}
    days = sorted(on_books)
    predicted = forecast.forecast_rooms(history, on_books, days)
    total = future["total_rooms"]
    rows = [{
        "date": d, "on_books": on_books[d], "forecast": predicted[d],
        "occupancy": (min(predicted[d], total) / total * 100) if total else 0,
    } for d in days]
    return render_template("forecast.html", rows=rows, total_rooms=total, history_days=len(history_rows),
                           has_history=any(history.values()))
