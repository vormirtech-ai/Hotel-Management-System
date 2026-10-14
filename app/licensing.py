"""License keys: Ed25519-signed payloads verified offline with a public key.

Key format:  HMS1-<base64url(json payload)>.<base64url(signature)>
Payload:     {"licensee", "edition", "expires" ("YYYY-MM-DD" or "never"),
              "install" (installation id or "*"), "max_rooms" (0 = unlimited), "issued"}

Only the vendor holds the private key (see tools/license_tool.py), so keys
cannot be forged from what ships with the app.
"""
import base64
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key

PREFIX = "HMS1-"
TRIAL_DAYS = 14
PUBLIC_KEY_FILE = Path(__file__).with_name("license_public_key.pem")


def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class LicenseStatus:
    valid: bool
    reason: str
    payload: dict = field(default_factory=dict)


def installation_id(data_dir):
    """Stable random id for this installation, used to bind licenses."""
    path = Path(data_dir) / "installation_id"
    if not path.exists():
        path.write_text(uuid.uuid4().hex[:16].upper(), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def verify_key(key, install_id, today=None, public_key_pem=None):
    key = (key or "").strip()
    if not key:
        return LicenseStatus(False, "No license key entered.")
    if not key.startswith(PREFIX) or "." not in key:
        return LicenseStatus(False, "This does not look like a valid license key.")
    try:
        body, sig = key[len(PREFIX):].split(".", 1)
        raw = b64d(body)
        pub = load_pem_public_key(public_key_pem or PUBLIC_KEY_FILE.read_bytes())
        pub.verify(b64d(sig), raw)
        payload = json.loads(raw)
    except InvalidSignature:
        return LicenseStatus(False, "License signature is invalid.")
    except (ValueError, TypeError, OSError):
        return LicenseStatus(False, "License key is damaged or incomplete.")
    if payload.get("install", "*") not in ("*", install_id):
        return LicenseStatus(False, "This license was issued for a different installation.", payload)
    expires = payload.get("expires", "never")
    if expires != "never" and (today or date.today()) > date.fromisoformat(expires):
        return LicenseStatus(False, f"License expired on {expires}.", payload)
    return LicenseStatus(True, "Licensed", payload)


def trial_days_left(trial_started, today=None):
    try:
        start = date.fromisoformat(trial_started)
    except (TypeError, ValueError):
        return 0
    end = start + timedelta(days=TRIAL_DAYS)
    return max(0, (end - (today or date.today())).days)
