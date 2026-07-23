"""
wsgi.py
Production entry point. Flask's built-in server (app.run in app.py) is
fine for local development but explicitly warns against production use.
This runs the same app through waitress, a pure-Python WSGI server that
works well on Windows (no compiler/extensions needed) as well as Linux/Mac.

Usage:
    pip install -r requirements.txt
    python wsgi.py

Then put a reverse proxy (nginx, IIS, Caddy) in front for HTTPS/TLS -
this process only serves plain HTTP.
"""

import os

from waitress import serve

from app import app

if __name__ == "__main__":
    host = os.environ.get("BMY_HOST", "0.0.0.0")
    port = int(os.environ.get("BMY_PORT", "5000"))
    threads = int(os.environ.get("BMY_THREADS", "4"))
    print(f"Serving BMY Marketer AI Assistant on http://{host}:{port} (waitress, {threads} threads)")
    serve(app, host=host, port=port, threads=threads)
