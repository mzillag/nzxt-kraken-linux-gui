"""Local HTTP API and static file server for the UI (standard library only).

Endpoints:
  GET  /                      UI
  GET  /api/state             full state (device, sensors, cooling, lcd, config)
  GET  /api/events            Server-Sent Events stream of state updates
  GET  /api/metrics           metric definitions
  GET  /api/config            config
  POST /api/config            JSON patch merged into the config and applied
  PUT  /api/config/<section>  replace a whole section
  POST /api/config/reset      {"section": "cooling"|"lcd"|null}
  GET  /api/lcd/preview.jpg   latest rendered frame
  POST /api/lcd/upload        raw file body, X-File-Name header; saves into the asset library
  GET  /api/lcd/library       uploaded files
  DELETE /api/lcd/library?path=...
  GET  /api/lcd/thumb?path=   128x128 thumbnail of a library file
  GET  /api/system/specs, /api/system/processes, /api/history
  POST /api/device/reconnect
  POST /api/quit
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import queue
import sys
import re
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

from . import metrics
from .config import ASSETS_DIR
from .daemon import Daemon, system_specs, top_processes

LOGGER = logging.getLogger(__name__)
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
ALLOWED_UPLOAD = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_UPLOAD = 120 * 1024 * 1024


class _QuietServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not print tracebacks for clients that went away."""

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class ApiServer:
    def __init__(self, daemon: Daemon, host: str = "127.0.0.1", port: int = 30100) -> None:
        self.daemon = daemon
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.on_quit = None

    def start(self) -> None:
        handler = _make_handler(self)
        self.httpd = _QuietServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        thread = threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True)
        thread.start()
        LOGGER.info("API listening on http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            # shutdown() only ends the serve loop; close the listening socket too so the
            # port is released even if the process lingers (for example while a window closes)
            self.httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


def _make_handler(server: ApiServer):
    daemon = server.daemon

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter logging
            LOGGER.debug("http: " + fmt, *args)

        # ---------------------------------------------------------- helpers
        def _json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, body: bytes, content_type: str, cache: str = "no-store"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status, message):
            self._json({"error": message}, status)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                raise ValueError("body too large")
            return self.rfile.read(length) if length else b""

        def _read_json(self):
            body = self._read_body()
            return json.loads(body.decode("utf-8")) if body else {}

        # ---------------------------------------------------------- routing
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if path == "/api/state":
                    return self._json(daemon.state())
                if path == "/api/events":
                    return self._sse()
                if path == "/api/metrics":
                    return self._json(metrics.as_dict())
                if path == "/api/config":
                    return self._json(daemon.config.get())
                if path == "/api/lcd/preview.jpg":
                    data, _ = daemon.lcd.preview_jpeg()
                    if not data:
                        img = Image.new("RGB", (320, 320), (0, 0, 0))
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG")
                        data = buf.getvalue()
                    return self._bytes(data, "image/jpeg")
                if path == "/api/lcd/library":
                    return self._json(_library())
                if path == "/api/lcd/thumb":
                    return self._thumb(query.get("path", [""])[0])
                if path == "/api/lcd/render":
                    return self._render(query)
                if path == "/api/autostart":
                    return self._json({"enabled": autostart_enabled()})
                if path == "/api/system/specs":
                    return self._json(system_specs(daemon))
                if path == "/api/system/processes":
                    return self._json(top_processes())
                if path == "/api/history":
                    return self._json(daemon.sensors.history())
                if path.startswith("/api/"):
                    return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
                return self._static(path)
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("GET %s failed", path)
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path == "/api/config":
                    patch = self._read_json()
                    if not isinstance(patch, dict):
                        return self._error(HTTPStatus.BAD_REQUEST, "expected an object")
                    return self._json(daemon.update_config(patch))
                if path == "/api/config/reset":
                    body = self._read_json()
                    section = body.get("section") if isinstance(body, dict) else None
                    daemon.config.reset(section)
                    if daemon.device is not None:
                        daemon.cooling.apply(force=True)
                    daemon.lcd.notify()
                    daemon.broadcast()
                    return self._json(daemon.config.get())
                if path == "/api/lcd/upload":
                    return self._upload()
                if path == "/api/autostart":
                    body = self._read_json()
                    enabled = bool(body.get("enabled")) if isinstance(body, dict) else False
                    return self._json(set_autostart(enabled))
                if path == "/api/device/reconnect":
                    ok = daemon.reconnect()
                    daemon.broadcast()
                    return self._json({"connected": ok, "error": daemon.device_error})
                if path == "/api/quit":
                    self._json({"ok": True})
                    if server.on_quit:
                        threading.Thread(target=server.on_quit, daemon=True).start()
                    return None
                return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("POST %s failed", path)
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_PUT(self):
            parsed = urllib.parse.urlparse(self.path)
            match = re.fullmatch(r"/api/config/(cooling|lcd|general|profiles)", parsed.path)
            if not match:
                return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            try:
                value = self._read_json()
                if not isinstance(value, dict):
                    return self._error(HTTPStatus.BAD_REQUEST, "expected an object")
                return self._json(daemon.replace_config_section(match.group(1), value))
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_DELETE(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/lcd/library":
                path = query.get("path", [""])[0]
                real = os.path.realpath(path)
                if real.startswith(os.path.realpath(ASSETS_DIR) + os.sep) and os.path.exists(real):
                    os.remove(real)
                    return self._json(_library())
                return self._error(HTTPStatus.BAD_REQUEST, "not a library file")
            return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

        # ---------------------------------------------------------- handlers
        def _sse(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q: queue.Queue = queue.Queue(maxsize=4)
            daemon.add_listener(q)
            try:
                self._write_event(daemon.state())
                while True:
                    try:
                        state = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self._write_event(state)
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass
            finally:
                daemon.remove_listener(q)

        def _write_event(self, state):
            payload = json.dumps(state).encode("utf-8")
            self.wfile.write(b"event: state\ndata: " + payload + b"\n\n")
            self.wfile.flush()

        def _static(self, path: str):
            if path == "/":
                path = "/index.html"
            safe = os.path.normpath(path).lstrip("/")
            full = os.path.join(WEB_DIR, safe)
            if not full.startswith(os.path.abspath(WEB_DIR)) or not os.path.isfile(full):
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.endswith(".woff2"):
                ctype = "font/woff2"
            with open(full, "rb") as fh:
                data = fh.read()
            cache = "no-cache" if full.endswith((".html", ".js", ".css")) else "max-age=86400"
            return self._bytes(data, ctype, cache)

        def _upload(self):
            name = urllib.parse.unquote(self.headers.get("X-File-Name") or "upload")
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name)) or "upload"
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_UPLOAD:
                return self._error(HTTPStatus.BAD_REQUEST, "unsupported file type")
            data = self._read_body()
            if not data:
                return self._error(HTTPStatus.BAD_REQUEST, "empty upload")
            try:
                Image.open(io.BytesIO(data)).verify()
            except Exception:  # noqa: BLE001
                return self._error(HTTPStatus.BAD_REQUEST, "file is not a valid image")
            os.makedirs(ASSETS_DIR, exist_ok=True)
            stem, _ = os.path.splitext(name)
            target = os.path.join(ASSETS_DIR, f"{stem}-{int(time.time())}{ext}")
            with open(target, "wb") as fh:
                fh.write(data)
            return self._json({"path": target, "library": _library()})

        def _render(self, query):
            """Render a screen specification to a small JPEG (carousel tiles, mode previews)."""
            try:
                spec = json.loads(query.get("spec", ["{}"])[0])
                size = max(32, min(640, int(query.get("size", ["128"])[0])))
            except (ValueError, TypeError):
                return self._error(HTTPStatus.BAD_REQUEST, "bad spec")
            if not isinstance(spec, dict):
                return self._error(HTTPStatus.BAD_REQUEST, "bad spec")
            unit = daemon.config.section("general").get("temperature_unit", "C")
            rotation = int(query.get("rotation", ["0"])[0] or 0)
            img = daemon.lcd.renderer.render(spec, daemon.sensors.snapshot(), unit, rotation=rotation)
            buf = io.BytesIO()
            img.resize((size, size), Image.LANCZOS).save(buf, format="JPEG", quality=85)
            return self._bytes(buf.getvalue(), "image/jpeg")

        def _thumb(self, path: str):
            real = os.path.realpath(path)
            if not real.startswith(os.path.realpath(ASSETS_DIR) + os.sep) or not os.path.isfile(real):
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            try:
                img = Image.open(real)
                thumb = ImageOps.fit(img.convert("RGB"), (128, 128), Image.LANCZOS)
            except Exception:  # noqa: BLE001
                return self._error(HTTPStatus.BAD_REQUEST, "cannot read image")
            buf = io.BytesIO()
            thumb.save(buf, format="JPEG", quality=80)
            return self._bytes(buf.getvalue(), "image/jpeg", "max-age=3600")

    def _library() -> list[dict]:
        if not os.path.isdir(ASSETS_DIR):
            return []
        items = []
        for name in sorted(os.listdir(ASSETS_DIR), key=lambda n: -os.path.getmtime(os.path.join(ASSETS_DIR, n))):
            full = os.path.join(ASSETS_DIR, name)
            if os.path.splitext(name)[1].lower() in ALLOWED_UPLOAD:
                items.append({"path": full, "name": name, "size": os.path.getsize(full),
                              "gif": name.lower().endswith(".gif")})
        return items

    return Handler


# ---------------------------------------------------------------------- autostart (systemd user unit)
UNIT_PATH = os.path.expanduser("~/.config/systemd/user/kraken-control.service")


def _unit_text() -> str:
    python = os.path.abspath(sys.executable)
    project = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return (
        "[Unit]\nDescription=Kraken Control daemon (NZXT Kraken Elite cooling and LCD)\n"
        "After=graphical-session.target\n\n[Service]\nType=simple\n"
        f"ExecStart={python} -m krakencontrol daemon\nWorkingDirectory={project}\n"
        "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=15\n\n[Install]\nWantedBy=default.target\n"
    )


def autostart_enabled() -> bool:
    import subprocess
    try:
        out = subprocess.run(["systemctl", "--user", "is-enabled", "kraken-control.service"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "enabled"
    except (OSError, subprocess.SubprocessError):
        return False


def set_autostart(enabled: bool) -> dict:
    import subprocess
    try:
        if enabled:
            os.makedirs(os.path.dirname(UNIT_PATH), exist_ok=True)
            with open(UNIT_PATH, "w", encoding="utf-8") as fh:
                fh.write(_unit_text())
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10, check=False)
            out = subprocess.run(["systemctl", "--user", "enable", "kraken-control.service"],
                                 capture_output=True, text=True, timeout=10)
        else:
            out = subprocess.run(["systemctl", "--user", "disable", "kraken-control.service"],
                                 capture_output=True, text=True, timeout=10)
        return {"enabled": autostart_enabled(), "output": (out.stdout + out.stderr).strip()}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"enabled": autostart_enabled(), "error": str(exc)}
