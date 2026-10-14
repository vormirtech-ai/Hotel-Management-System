"""AI review writer: balanced product reviews generated with Google Gemini.

Input:  productName, productCategory, keyFeatures (list of strings)
Output: {"headline": str, "body": str}
Errors: ReviewError carrying a user-safe message and an HTTP status code.
"""
import json
import logging

log = logging.getLogger(__name__)

MAX_FEATURES = 10

# Block harmful content at medium probability and above in every category.
SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
BLOCKED_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"headline": {"type": "STRING"}, "body": {"type": "STRING"}},
    "required": ["headline", "body"],
}


class ReviewError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.message, self.status = message, status


def validate_request(data):
    """Check and normalise the JSON request body (trust boundary)."""
    if not isinstance(data, dict):
        raise ReviewError("Request body must be a JSON object.", 400)
    name = data.get("productName")
    category = data.get("productCategory")
    features = data.get("keyFeatures")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ReviewError("productName is required (max 120 characters).", 400)
    if not isinstance(category, str) or not category.strip() or len(category) > 80:
        raise ReviewError("productCategory is required (max 80 characters).", 400)
    if not isinstance(features, list) or not features:
        raise ReviewError("keyFeatures must be a non-empty list of strings.", 400)
    features = [f.strip() for f in features if isinstance(f, str) and f.strip()]
    if not features or len(features) > MAX_FEATURES or any(len(f) > 120 for f in features):
        raise ReviewError(f"Provide 1 to {MAX_FEATURES} key features, each up to 120 characters.", 400)
    return name.strip(), category.strip(), features


def build_prompt(name, category, features):
    feature_list = "\n".join(f"- {f}" for f in features)
    return (
        "You are an experienced, fair-minded reviewer writing for real customers.\n"
        f"Write a balanced, realistic review of the {category} product named \"{name}\".\n\n"
        "Base the review ONLY on these key features (do not invent specifications, prices or awards):\n"
        f"{feature_list}\n\n"
        "Guidelines:\n"
        f"- Judge the features by what matters to buyers in the {category} category.\n"
        "- Be balanced: praise genuine strengths and mention at least one honest limitation, "
        "trade-off or kind of buyer it may not suit.\n"
        "- Natural, first-person-reviewer tone; no marketing hype, no emojis, no profanity.\n"
        "- headline: one catchy sentence that summarises the verdict (max 15 words).\n"
        "- body: one paragraph of 120 to 180 words.\n"
        "Return only JSON with the fields \"headline\" and \"body\"."
    )


def generate_review(api_key, model, name, category, features, client=None):
    """Call Gemini and return {"headline", "body"}; raise ReviewError on any failure."""
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError:
        raise ReviewError("The Gemini SDK is not installed. Run: pip install google-genai", 503)
    if client is None and not api_key:
        raise ReviewError("Gemini API key is not configured. Add it under Setup → AI settings.", 503)

    config = types.GenerateContentConfig(
        temperature=0.7,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        safety_settings=[
            types.SafetySetting(category=getattr(types.HarmCategory, c),
                                threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE)
            for c in SAFETY_CATEGORIES
        ],
    )
    try:
        client = client or genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model, contents=build_prompt(name, category, features), config=config)
    except errors.ClientError as exc:
        log.warning("Gemini client error %s: %s", exc.code, exc)
        if exc.code == 429:
            raise ReviewError("Gemini rate limit or quota reached. Please wait a minute and try again.", 429)
        if exc.code in (401, 403):
            raise ReviewError("Gemini rejected the API key. Check it under Setup → AI settings.", 502)
        if exc.code == 404:
            raise ReviewError(f"Gemini model '{model}' was not found. Choose another model in AI settings.", 502)
        raise ReviewError("Gemini could not process this request.", 502)
    except errors.ServerError as exc:
        log.warning("Gemini server error %s: %s", exc.code, exc)
        raise ReviewError("Gemini is temporarily unavailable. Please try again shortly.", 503)
    except Exception:  # network failures, timeouts, unexpected SDK errors
        log.exception("Gemini request failed")
        raise ReviewError("Could not reach Gemini. Check the internet connection and try again.", 503)

    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise ReviewError("The request was blocked by the content safety filter.", 422)
    candidates = getattr(response, "candidates", None) or []
    finish = getattr(getattr(candidates[0], "finish_reason", None), "name", "") if candidates else ""
    if finish in BLOCKED_FINISH:
        raise ReviewError("The generated review was blocked by the content safety filter.", 422)

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise ReviewError("Gemini returned an empty response. Please try again.", 502)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Gemini returned non-JSON output (finish=%s)", finish)
        raise ReviewError("Gemini returned an incomplete review. Please try again.", 502)
    headline = data.get("headline") if isinstance(data, dict) else None
    body = data.get("body") if isinstance(data, dict) else None
    if not (isinstance(headline, str) and headline.strip() and isinstance(body, str) and body.strip()):
        raise ReviewError("Gemini returned an incomplete review. Please try again.", 502)
    return {"headline": headline.strip(), "body": body.strip()}
