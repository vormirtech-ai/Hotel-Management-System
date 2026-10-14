"""AI review writer page and its JSON API."""
from flask import Blueprint, render_template, request

from ..db import get_db, get_settings
from ..review_ai import ReviewError, generate_review, validate_request

bp = Blueprint("ai", __name__)


@bp.route("/ai/review-writer")
def review_writer():
    db = get_db()
    presets = [{"name": r["name"], "category": "Hotel room",
                "features": [a.strip() for a in r["amenities"].split(",") if a.strip()][:10]}
               for r in db.execute("SELECT name, amenities FROM room_types ORDER BY name")]
    presets += [{"name": v["name"], "category": "Banquet hall" if v["kind"] == "banquet" else "Event lawn",
                 "features": [f"Capacity of {v['capacity']} guests"] if v["capacity"] else []}
                for v in db.execute("SELECT name, kind, capacity FROM venues WHERE active = 1 ORDER BY name")]
    configured = bool(get_settings().get("gemini_api_key"))
    return render_template("review_writer.html", presets=presets, configured=configured)


@bp.route("/api/reviews/generate", methods=["POST"])
def api_generate():
    """POST JSON {productName, productCategory, keyFeatures[]} -> {headline, body}."""
    try:
        name, category, features = validate_request(request.get_json(silent=True))
        settings = get_settings()
        review = generate_review(settings.get("gemini_api_key"), settings.get("gemini_model") or "gemini-2.5-flash",
                                 name, category, features)
        return review, 200
    except ReviewError as exc:
        return {"error": exc.message}, exc.status
