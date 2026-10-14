"""Vendor-side license tool. Keep the private key OUT of customer deliveries.

    python tools/license_tool.py keygen                 # new key pair (rotates app public key)
    python tools/license_tool.py issue --licensee "Hotel Sunrise" --expires 2027-12-31 \
        [--install ABCD1234EF567890] [--max-rooms 50] [--edition standard]
    python tools/license_tool.py verify HMS1-...
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.licensing import PREFIX, PUBLIC_KEY_FILE, b64e, verify_key  # noqa: E402

DEFAULT_PRIVATE = ROOT / "vendor_keys" / "license_private_key.pem"


def keygen(args):
    out = Path(args.private_key)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists. Use --force to replace it (all issued keys stop working).")
    key = Ed25519PrivateKey.generate()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    PUBLIC_KEY_FILE.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"Private key: {out}  (keep secret)\nPublic key:  {PUBLIC_KEY_FILE}  (ships with the app)")


def issue(args):
    if args.expires != "never":
        date.fromisoformat(args.expires)  # validates format
    key = serialization.load_pem_private_key(Path(args.private_key).read_bytes(), password=None)
    payload = {
        "licensee": args.licensee,
        "edition": args.edition,
        "expires": args.expires,
        "install": args.install,
        "max_rooms": args.max_rooms,
        "issued": date.today().isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    print(PREFIX + b64e(raw) + "." + b64e(key.sign(raw)))


def verify(args):
    status = verify_key(args.key, args.install)
    print(json.dumps({"valid": status.valid, "reason": status.reason, "payload": status.payload}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--private-key", default=DEFAULT_PRIVATE)
    k.add_argument("--force", action="store_true")
    k.set_defaults(func=keygen)
    i = sub.add_parser("issue")
    i.add_argument("--private-key", default=DEFAULT_PRIVATE)
    i.add_argument("--licensee", required=True)
    i.add_argument("--expires", default="never", help="YYYY-MM-DD or 'never'")
    i.add_argument("--install", default="*", help="Installation ID shown on the License page, or * for any")
    i.add_argument("--max-rooms", type=int, default=0, help="0 = unlimited")
    i.add_argument("--edition", default="standard")
    i.set_defaults(func=issue)
    v = sub.add_parser("verify")
    v.add_argument("key")
    v.add_argument("--install", default="*")
    v.set_defaults(func=verify)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
