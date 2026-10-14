"""Sign in, first-run setup, password change and user management."""
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import admin_required
from ..db import get_db, save_settings
from ..forms import Field, parse

bp = Blueprint("auth", __name__)

MIN_PASSWORD = 8


def _login(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]


@bp.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.first_run"))
    if request.method == "POST":
        user = db.execute("SELECT * FROM users WHERE username = ? AND active = 1",
                          (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            _login(user)
            nxt = request.args.get("next", "")
            # Only follow local paths to avoid open redirects.
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("front.dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/welcome", methods=["GET", "POST"])
def first_run():
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.login"))
    errors = {}
    if request.method == "POST":
        f = request.form
        hotel, name, username, pw = (f.get(k, "").strip() for k in ("hotel_name", "full_name", "username", "password"))
        if not hotel:
            errors["hotel_name"] = "Please enter the hotel name."
        if not username or len(username) > 40:
            errors["username"] = "Choose a username (max 40 characters)."
        if len(pw) < MIN_PASSWORD:
            errors["password"] = f"Use at least {MIN_PASSWORD} characters."
        elif pw != f.get("confirm", ""):
            errors["confirm"] = "Passwords do not match."
        if not errors:
            db.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                       (username, name or username, generate_password_hash(pw)))
            save_settings({"hotel_name": hotel})  # commits
            _login(db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())
            flash("Welcome! Next, add your floors, room types and rooms under Setup.", "success")
            return redirect(url_for("front.dashboard"))
    return render_template("first_run.html", errors=errors, form=request.form)


@bp.route("/account/password", methods=["GET", "POST"])
def password():
    errors = {}
    if request.method == "POST":
        f = request.form
        if not check_password_hash(g.user["password_hash"], f.get("current", "")):
            errors["current"] = "Current password is incorrect."
        elif len(f.get("new", "")) < MIN_PASSWORD:
            errors["new"] = f"Use at least {MIN_PASSWORD} characters."
        elif f.get("new") != f.get("confirm"):
            errors["confirm"] = "Passwords do not match."
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(f["new"]), g.user["id"]))
            db.commit()
            flash("Password changed.", "success")
            return redirect(url_for("front.dashboard"))
    return render_template("password.html", errors=errors)


def _user_fields(new):
    return [
        Field("username", "Username", required=True, maxlength=40, width="half"),
        Field("full_name", "Full name", maxlength=80, width="half"),
        Field("role", "Role", "select", required=True, options=[("staff", "Front desk staff"), ("admin", "Administrator")],
              help="Administrators can change setup, view reports and manage the license.", width="half"),
        Field("active", "Account active", "bool", width="half"),
        Field("password", "Password" if new else "New password", "password", required=new,
              help="" if new else "Leave blank to keep the current password.", width="half"),
    ]


@bp.route("/setup/users")
@admin_required
def users():
    rows = get_db().execute("SELECT * FROM users ORDER BY active DESC, username").fetchall()
    return render_template("users.html", rows=rows)


@bp.route("/setup/users/new", methods=["GET", "POST"], defaults={"user_id": None})
@bp.route("/setup/users/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user_form(user_id):
    db = get_db()
    row = None
    if user_id:
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() or abort(404)
    fields = _user_fields(new=row is None)
    values, errors = (dict(row) if row else {"role": "staff", "active": 1}), {}
    if request.method == "POST":
        values, errors = parse(fields, request.form)
        pw = values.pop("password", "")
        if pw and len(pw) < MIN_PASSWORD:
            errors["password"] = f"Use at least {MIN_PASSWORD} characters."
        if row and row["id"] == g.user["id"] and (values.get("role") != "admin" or not values.get("active")):
            errors["role"] = "You cannot remove your own administrator access."
        if not errors:
            try:
                if row:
                    db.execute("UPDATE users SET username = ?, full_name = ?, role = ?, active = ? WHERE id = ?",
                               (values["username"], values["full_name"], values["role"], values["active"], row["id"]))
                    if pw:
                        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                                   (generate_password_hash(pw), row["id"]))
                else:
                    db.execute("INSERT INTO users (username, full_name, role, active, password_hash) VALUES (?, ?, ?, ?, ?)",
                               (values["username"], values["full_name"], values["role"], values["active"],
                                generate_password_hash(pw)))
                db.commit()
                flash("User saved.", "success")
                return redirect(url_for("auth.users"))
            except sqlite3.IntegrityError:
                db.rollback()
                errors["username"] = "That username is already taken."
    return render_template("form.html", title=("Edit user" if row else "New user"), fields=fields,
                           values=values, errors=errors, back=url_for("auth.users"))
