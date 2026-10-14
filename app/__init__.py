"""Hotel Management System – Flask application factory."""
import os
import secrets
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

from . import db as database
from . import licensing

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_ENDPOINTS = {"static", "auth.login", "auth.first_run", "media"}
LICENSE_FREE_ENDPOINTS = PUBLIC_ENDPOINTS | {"auth.logout", "setup.license", "auth.password"}


def create_app(test_config=None):
    app = Flask(__name__)
    data_dir = Path(os.environ.get("HMS_DATA_DIR") or ROOT / "instance")
    app.config.update(
        DATA_DIR=data_dir,
        DATABASE=data_dir / "hotel.db",
        UPLOAD_DIR=data_dir / "uploads",
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=12 * 3600,
    )
    if test_config:
        app.config.update(test_config)
    data_dir = Path(app.config["DATA_DIR"])
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    app.secret_key = os.environ.get("HMS_SECRET_KEY") or _load_secret(data_dir)
    database.init_db(app.config["DATABASE"])
    app.config["INSTALL_ID"] = licensing.installation_id(data_dir)
    app.teardown_appcontext(database.close_db)

    from .views import ai, auth, front, inventory, reports, setup
    for bp in (auth.bp, front.bp, inventory.bp, setup.bp, reports.bp, ai.bp):
        app.register_blueprint(bp)

    @app.route("/media/<path:name>")
    def media(name):
        from flask import send_from_directory
        return send_from_directory(app.config["UPLOAD_DIR"], name, max_age=3600)

    app.before_request(_before_request)
    app.context_processor(_template_context)
    app.jinja_env.filters.update(money=format_money, num=format_number, dmy=format_date, dt=format_datetime,
                                  todate=lambda v: date.fromisoformat(str(v)[:10]))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def http_error(err):
        if request.path.startswith("/api/"):
            return {"error": err.description}, err.code
        return render_template("error.html", error=err), err.code

    return app


def _load_secret(data_dir):
    path = data_dir / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def license_state():
    """(status, trial_days_left) for the current request, cached on g."""
    if "license" not in g:
        settings = database.get_settings()
        status = licensing.verify_key(settings["license_key"], current_install_id())
        g.license = (status, licensing.trial_days_left(settings["trial_started"]))
    return g.license


def current_install_id():
    from flask import current_app
    return current_app.config["INSTALL_ID"]


def _before_request():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = database.get_db().execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (uid,)).fetchone()
        if g.user is None:
            session.clear()

    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf_token", "")):
            abort(400, "Your session expired or the form is out of date. Please go back, reload and try again.")

    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS:
        return None
    if g.user is None:
        if not database.get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return redirect(url_for("auth.first_run"))
        if request.path.startswith("/api/"):
            return {"error": "Please sign in."}, 401
        return redirect(url_for("auth.login", next=request.full_path))

    status, trial_left = license_state()
    if not status.valid and trial_left <= 0 and endpoint not in LICENSE_FREE_ENDPOINTS:
        flash("The trial period has ended. Please activate a license to continue.", "error")
        return redirect(url_for("setup.license"))
    return None


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _template_context():
    ctx = {"csrf_token": csrf_token, "user": g.get("user"), "today": date.today()}
    if g.get("user"):
        ctx["settings"] = database.get_settings()
        ctx["license_status"], ctx["trial_left"] = license_state()
    else:
        try:
            ctx["settings"] = database.get_settings()
        except Exception:  # e.g. error page before the DB is reachable
            ctx["settings"] = database.DEFAULT_SETTINGS
    return ctx


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None or g.user["role"] != "admin":
            abort(403, "Only administrators can open this page.")
        return view(*args, **kwargs)
    return wrapped


def indian_grouping(value, decimals=2):
    neg = value < 0
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = [head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1]
        whole = ",".join(groups) + "," + tail
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def format_money(value, symbol=None):
    if symbol is None:
        symbol = database.get_settings().get("currency_symbol", "₹")
    value = float(value or 0)
    text = indian_grouping(value)
    return f"-{symbol}{text[1:]}" if text.startswith("-") else f"{symbol}{text}"


def format_number(value, decimals=0):
    return indian_grouping(float(value or 0), decimals)


def format_date(value):
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def format_datetime(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return str(value)
