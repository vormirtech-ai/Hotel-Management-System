#!/usr/bin/env sh
# Hotel Management System - start on macOS / Linux
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check -q -r requirements.txt
exec python run.py "$@"
