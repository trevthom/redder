#!/usr/bin/env python3
"""Redline: a local control surface for the X11 GPU color lookup table."""

from __future__ import annotations

import argparse
import atexit
import json
import mimetypes
import signal
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from gamma_control import GammaController, GammaError, query_outputs

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_PORT = 7331
MAX_BODY = 16 * 1024


class RedlineServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, controller, init_error, outputs):
        super().__init__(address, handler)
        self.controller = controller
        self.init_error = init_error
        self.outputs = outputs

    def payload(self) -> dict:
        controller = self.controller
        if controller is None:
            return {
                "supported": False,
                "error": self.init_error or "The display is unavailable.",
                "percent": 0,
                "channels": {"red": 100.0, "green": 100.0, "blue": 100.0},
                "outputs": self.outputs,
                "crtcCount": 0,
                "platform": "X11 / XRandR",
            }
        return {
            "supported": True,
            "error": self.init_error,
            "percent": round(controller.percent),
            "channels": controller.channel_percentages(),
            "outputs": self.outputs,
            "crtcCount": controller.crtc_count,
            "platform": "X11 / XRandR",
        }


class RedlineHandler(BaseHTTPRequestHandler):
    server: RedlineServer
    protocol_version = "HTTP/1.1"
    server_version = "Redline/1.0"

    # -- response helpers ---------------------------------------------

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _send_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
            return
        content_type, _ = mimetypes.guess_type(path.name)
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "image/svg+xml",
        }:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._common_headers()
        if path.name == "index.html":
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'",
            )
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
            return
        self._send_file(candidate)

    # -- request hooks ------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/state":
            self._send_json(HTTPStatus.OK, {"ok": True, **self.server.payload()})
            return
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/api/"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/apply", "/api/reset"}:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
            return

        origin = self.headers.get("Origin")
        if origin:
            port = self.server.server_address[1]
            allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed:
                self._send_error_json(HTTPStatus.FORBIDDEN, "Cross-origin request refused.")
                return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_error_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected a JSON request.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length.")
            return
        if length < 0 or length > MAX_BODY:
            self._send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large.")
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON.")
            return
        if not isinstance(data, dict):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
            return

        controller = self.server.controller
        if controller is None:
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                self.server.init_error or "The display is unavailable.",
            )
            return

        try:
            if path == "/api/apply":
                percent = data.get("percent")
                if isinstance(percent, bool) or not isinstance(percent, (int, float)):
                    self._send_error_json(
                        HTTPStatus.BAD_REQUEST, "percent must be a number from 0 to 100."
                    )
                    return
                if not 0 <= percent <= 100:
                    self._send_error_json(
                        HTTPStatus.BAD_REQUEST, "percent must be between 0 and 100."
                    )
                    return
                controller.apply_percent(float(percent))
            else:
                controller.restore()
        except GammaError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        except Exception:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, "The display update failed unexpectedly."
            )
            return

        self._send_json(HTTPStatus.OK, {"ok": True, **self.server.payload()})

    def log_message(self, _format: str, *_args) -> None:
        # Keeps the terminal readable while the slider streams changes.
        return


def _shutdown_for_signal(_signum, _frame) -> None:
    raise KeyboardInterrupt


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Control the X11 GPU color table and isolate the red channel."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="localhost port (default: 7331)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="write a standard linear gamma ramp and exit (crash recovery)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def reset_display() -> int:
    try:
        controller = GammaController(capture_originals=False)
    except GammaError as exc:
        print(f"Could not reset the display: {exc}", file=sys.stderr)
        return 1
    try:
        controller.apply_identity()
    except GammaError as exc:
        print(f"Could not reset the display: {exc}", file=sys.stderr)
        return 1
    finally:
        controller.close(restore=False)
    print("Display gamma reset to a standard linear ramp.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.reset:
        return reset_display()

    controller: GammaController | None = None
    init_error: str | None = None
    try:
        controller = GammaController()
    except GammaError as exc:
        init_error = str(exc)

    outputs = query_outputs()
    try:
        httpd = RedlineServer(
            ("127.0.0.1", args.port),
            RedlineHandler,
            controller,
            init_error,
            outputs,
        )
    except OSError as exc:
        if controller is not None:
            controller.close()
        print(
            f"Could not listen on http://127.0.0.1:{args.port}: {exc}\n"
            "Redline may already be running, or the port is in use. Try --port 7332.",
            file=sys.stderr,
        )
        return 1

    def cleanup() -> None:
        if controller is not None:
            controller.close()

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, _shutdown_for_signal)
    signal.signal(signal.SIGTERM, _shutdown_for_signal)

    url = f"http://127.0.0.1:{args.port}/"
    print("", flush=True)
    print("  REDLINE  /  GPU color table control", flush=True)
    print(f"  {url}", flush=True)
    if controller is not None:
        display_names = ", ".join(
            f"{item['name']}{' ' + item['resolution'] if item['resolution'] else ''}"
            for item in outputs
        ) or "X11 display"
        print(f"  Connected: {display_names}  ({controller.crtc_count} gamma tables)", flush=True)
        print("  Press Ctrl+C to restore full color and quit.", flush=True)
    else:
        print(f"  Display unavailable: {init_error}", flush=True)
    print("", flush=True)

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    interrupted = False
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        # A second Ctrl+C must not interrupt the GPU restore on the way out.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        httpd.server_close()
        if controller is not None:
            controller.close()
            print("Display restored to its original color table.", flush=True)
    if interrupted:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
