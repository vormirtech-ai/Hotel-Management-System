"""Hotel profile, billing rules, AI settings, license activation and backup."""
import io
import secrets
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .. import admin_required, current_install_id, licensing
from ..db import get_settings, save_settings
from ..forms import Field, parse, section

bp = Blueprint("setup", __name__, url_prefix="/setup")

LOGO_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

PROFILE_FIELDS = [
    section("Identity"),
    Field("hotel_name", "Hotel name", required=True, maxlength=120, width="half"),
    Field("hotel_tagline", "Tagline", maxlength=120, width="half"),
    Field("hotel_logo", "Logo", "file", help="PNG, JPG, GIF or WebP, up to 4 MB. Shown on screens and invoices."),
    section("Address & contact"),
    Field("hotel_address", "Street address", "textarea"),
    Field("hotel_city", "City", width="half"),
    Field("hotel_state", "State", width="half"),
    Field("hotel_pincode", "PIN code", maxlength=12, width="half"),
    Field("hotel_phone", "Phone", maxlength=40, width="half"),
    Field("hotel_email", "Email", "email", width="half"),
    Field("hotel_website", "Website", width="half"),
    section("Tax registration"),
    Field("hotel_gstin", "GSTIN", maxlength=15, placeholder="22AAAAA0000A1Z5", width="half"),
    Field("hotel_state_code", "State code", maxlength=4, placeholder="e.g. 27", width="half"),
    Field("hotel_pan", "PAN", maxlength=10, width="half"),
    Field("currency_symbol", "Currency symbol", required=True, maxlength=4, width="half"),
]

BILLING_FIELDS = [
    section("Stay timings"),
    Field("check_in_time", "Standard check-in time", "time", required=True, width="half"),
    Field("check_out_time", "Standard check-out time", "time", required=True, width="half"),
    Field("late_checkout_fee_percent", "Late check-out fee (% of one night)", "float", min=0, max=100,
          help="0 disables the fee. Suggested automatically at check-out.", width="half"),
    Field("late_checkout_grace_hours", "Grace period (hours)", "float", min=0, max=24, width="half"),
    section("Invoices"),
    Field("invoice_prefix", "Invoice number prefix", maxlength=12, width="half"),
    Field("invoice_next_number", "Next invoice number", "int", required=True, min=1, width="half"),
    Field("round_off", "Round invoice totals to the nearest rupee", "bool"),
    Field("sac_room", "SAC code – rooms", maxlength=10, width="half"),
    Field("sac_venue", "SAC code – banquets / lawns", maxlength=10, width="half"),
    Field("sac_extra", "SAC code – extras", maxlength=10, width="half"),
    Field("invoice_terms", "Invoice footer / terms", "textarea"),
]

AI_FIELDS = [
    Field("gemini_api_key", "Gemini API key", "password",
          help="Create one at aistudio.google.com. Stored only in your local database. Leave blank to keep the saved key."),
    Field("gemini_model", "Model", required=True, maxlength=60,
          help="The brief named gemini-1.5-flash; Google has retired it, so gemini-2.5-flash is the default."),
]


def _settings_form(fields, title, intro, endpoint):
    settings = get_settings()
    values, errors = dict(settings), {}
    if request.method == "POST":
        values, errors = parse(fields, request.form)
        if endpoint == "setup.ai" and not values.get("gemini_api_key"):
            values.pop("gemini_api_key")  # keep the saved key
        logo = request.files.get("hotel_logo")
        if logo and logo.filename:
            ext = Path(logo.filename).suffix.lower()
            if ext not in LOGO_TYPES:
                errors["hotel_logo"] = "Please upload a PNG, JPG, GIF or WebP image."
            else:
                name = f"logo_{secrets.token_hex(6)}{ext}"
                logo.save(Path(current_app.config["UPLOAD_DIR"]) / name)
                values["hotel_logo"] = name
        if request.form.get("remove_logo"):
            values["hotel_logo"] = ""
        if not errors:
            save_settings(values)
            flash("Settings saved.", "success")
            return redirect(url_for(endpoint))
    if endpoint == "setup.ai":
        values["gemini_api_key"] = ""
    return render_template("form.html", title=title, intro=intro, fields=fields, values=values, errors=errors,
                           multipart=True, saved_logo=settings.get("hotel_logo"))


@bp.route("/hotel", methods=["GET", "POST"])
@admin_required
def profile():
    return _settings_form(PROFILE_FIELDS, "Hotel profile", "These details appear on invoices and reports.",
                          "setup.profile")


@bp.route("/billing", methods=["GET", "POST"])
@admin_required
def billing_rules():
    return _settings_form(BILLING_FIELDS, "Billing rules", "Timings, invoice numbering and fees.",
                          "setup.billing_rules")


@bp.route("/ai", methods=["GET", "POST"])
@admin_required
def ai():
    key_set = bool(get_settings().get("gemini_api_key"))
    return _settings_form(AI_FIELDS, "AI settings",
                          "Connect Google Gemini for the AI review writer. "
                          + ("An API key is saved." if key_set else "No API key saved yet."), "setup.ai")


@bp.route("/license", methods=["GET", "POST"])
def license():
    install_id = current_install_id()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can activate a license.", "error")
            return redirect(url_for("setup.license"))
        key = request.form.get("license_key", "").strip()
        status = licensing.verify_key(key, install_id)
        if status.valid:
            save_settings({"license_key": key})
            flash(f"License activated for {status.payload.get('licensee')}.", "success")
            return redirect(url_for("setup.license"))
        flash(status.reason, "error")
    settings = get_settings()
    status = licensing.verify_key(settings["license_key"], install_id)
    return render_template("license.html", status=status, install_id=install_id,
                           trial_left=licensing.trial_days_left(settings["trial_started"]),
                           has_key=bool(settings["license_key"]))


@bp.route("/backup")
@admin_required
def backup():
    return render_template("backup.html", db_path=current_app.config["DATABASE"])


@bp.route("/backup/download", methods=["POST"])
@admin_required
def backup_download():
    # SQLite's online backup gives a consistent copy even while the app is in use.
    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / "backup.db"
    try:
        src, dst = sqlite3.connect(current_app.config["DATABASE"]), sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        data = tmp.read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return send_file(io.BytesIO(data), as_attachment=True, mimetype="application/vnd.sqlite3",
                     download_name=f"hotel-backup-{datetime.now():%Y%m%d-%H%M}.db")
