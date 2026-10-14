"""Start the Hotel Management System.

    python run.py              # production server (waitress) on http://127.0.0.1:8000
    python run.py --dev        # Flask development server with auto-reload

Environment: HMS_HOST, HMS_PORT, HMS_DATA_DIR (database + uploads folder), HMS_SECRET_KEY.
"""
import os
import sys

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HMS_HOST", "127.0.0.1")
    port = int(os.environ.get("HMS_PORT", "8000"))
    if "--dev" in sys.argv:
        app.run(host=host, port=port, debug=True)
    else:
        from waitress import serve
        print(f"Hotel Management System running on http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
        print("Press Ctrl+C to stop.")
        serve(app, host=host, port=port, threads=8)
