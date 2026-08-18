"""Render web service: serves the WEB ADMIN PANEL + health endpoints,
and keeps the free instance awake with a best-effort self-pinger.

The Telegram bot (bot.py) starts this in a background thread, so a single
Render web service runs both the bot and the browser admin panel.
"""

import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import sleep
from urllib.error import URLError
from urllib.request import Request, urlopen


logger = logging.getLogger("render_server")
SELF_PING_INTERVAL_SECONDS = 120


class HealthHandler(BaseHTTPRequestHandler):
    """Fallback server used only if Flask/waitress are unavailable."""

    def do_GET(self):
        if self.path not in ("/", "/healthz", "/health"):
            self.send_response(404)
            self.end_headers()
            return

        body = b'{"ok":true,"service":"telegram-bot"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _serve_panel(port):
    from waitress import serve
    from webpanel.app import create_app

    app = create_app()
    logger.info("Web admin panel listening on port %s", port)
    serve(app, host="0.0.0.0", port=port, threads=16,
          channel_timeout=120, connection_limit=200, asyncore_use_poll=True)


def start_health_server():
    """Start the web admin panel (preferred) or the plain health server."""
    port = int(os.environ.get("PORT", "10000"))

    try:
        import waitress  # noqa: F401
        from webpanel.app import create_app  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web panel unavailable (%s) — starting health server only", exc)
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
        Thread(target=server.serve_forever, name="render-health", daemon=True).start()
        _start_self_ping(port)
        return server

    Thread(target=_serve_panel, args=(port,), name="web-admin-panel",
           daemon=True).start()
    _start_self_ping(port)
    return None


def _self_ping_url(port):
    explicit = os.environ.get("SELF_PING_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url.rstrip("/") + "/healthz"

    return f"http://127.0.0.1:{port}/healthz"


def _self_ping_loop(url):
    while True:
        try:
            request = Request(
                url,
                headers={"User-Agent": "nexus-store-health-check/1.0"},
            )
            with urlopen(request, timeout=15) as response:
                response.read(128)
            logger.debug("Self-ping succeeded: %s", url)
        except (OSError, URLError, TimeoutError) as exc:
            logger.warning("Self-ping failed for %s: %s", url, exc)
        except Exception:
            logger.exception("Unexpected self-ping error for %s", url)
        sleep(SELF_PING_INTERVAL_SECONDS)


def _start_self_ping(port):
    url = _self_ping_url(port)
    Thread(
        target=_self_ping_loop,
        args=(url,),
        name="render-self-ping",
        daemon=True,
    ).start()
    logger.info(
        "Self-ping enabled: %s every %s seconds",
        url,
        SELF_PING_INTERVAL_SECONDS,
    )
