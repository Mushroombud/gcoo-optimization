#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common import write_json  # noqa: E402
from visualize_optimization_model import PARAMETER_SEARCH_TRIALS, run_parameter_search  # noqa: E402


UTF8_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

PARAMETER_SEARCH_LOCK = threading.Lock()
HERMES_BRIDGE_HOST = "127.0.0.1"
HERMES_BRIDGE_PORT = 8787
HERMES_BRIDGE_TIMEOUT_SECONDS = 1800
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class Utf8StaticHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        **UTF8_TYPES,
    }

    def guess_type(self, path: str) -> str:
        lower_path = path.lower()
        for suffix, content_type in self.extensions_map.items():
            if lower_path.endswith(suffix):
                return content_type
        return super().guess_type(path)

    def send_json(self, payload: dict, status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.proxy_to_hermes_bridge("GET")
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/parameter-search":
            if parsed.path.startswith("/api/"):
                self.proxy_to_hermes_bridge("POST", self.read_body())
                return
            self.send_json({"ok": False, "error": "Unknown API endpoint"}, status=404)
            return

        if not PARAMETER_SEARCH_LOCK.acquire(blocking=False):
            self.send_json(
                {"ok": False, "error": "Parameter calibration simulation is already running."},
                status=409,
            )
            return

        try:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                content_length = max(0, int(raw_length))
            except ValueError:
                content_length = 0
            body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                request_payload = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                request_payload = {}
            trial_count = int(request_payload.get("trial_count", PARAMETER_SEARCH_TRIALS))
            trial_count = max(1, trial_count)
            lambda_step = float(request_payload.get("lambda_step", 0.05))
            beta_step = float(request_payload.get("beta_step", 0.005))
            theta_step = float(request_payload.get("theta_step", 0.05))
            allow_long_run = bool(request_payload.get("allow_long_run", True))
            raw_max_workers = request_payload.get("max_workers")
            max_workers = int(raw_max_workers) if raw_max_workers is not None else None

            processed_dir = REPO_ROOT / "data" / "processed" / "sejong_tago"
            output_dir = Path(getattr(self, "directory", REPO_ROOT / "outputs" / "visualizations")).resolve()
            result = run_parameter_search(
                processed_dir,
                trial_count,
                lambda_step,
                beta_step,
                theta_step,
                allow_long_run=allow_long_run,
                max_workers=max_workers,
            )
            write_json(output_dir / "parameter_search_results.json", result)
            self.send_json(result, status=200)
        except Exception as exc:  # noqa: BLE001
            self.log_error("parameter search failed: %s", exc)
            self.send_json({"ok": False, "error": str(exc)}, status=500)
        finally:
            PARAMETER_SEARCH_LOCK.release()

    def read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = max(0, int(raw_length))
        except ValueError:
            content_length = 0
        return self.rfile.read(content_length) if content_length else b""

    def proxy_to_hermes_bridge(self, method: str, body: bytes | None = None) -> None:
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() not in {"host", "content-length"}
        }
        if body is not None:
            headers["Content-Length"] = str(len(body))
        headers["Host"] = f"{HERMES_BRIDGE_HOST}:{HERMES_BRIDGE_PORT}"

        connection = http.client.HTTPConnection(
            HERMES_BRIDGE_HOST,
            HERMES_BRIDGE_PORT,
            timeout=HERMES_BRIDGE_TIMEOUT_SECONDS,
        )
        headers_sent = False
        try:
            connection.request(method, self.path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "")
            is_event_stream = content_type.startswith("text/event-stream")

            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                lower_key = key.lower()
                if lower_key in HOP_BY_HOP_HEADERS:
                    continue
                if is_event_stream and lower_key == "content-length":
                    continue
                self.send_header(key, value)
            self.end_headers()
            headers_sent = True

            if is_event_stream:
                while True:
                    line = response.fp.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                self.wfile.write(response.read())
        except BrokenPipeError:
            return
        except (OSError, http.client.HTTPException) as exc:
            self.log_error("Hermes bridge proxy failed: %s", exc)
            if not headers_sent:
                self.send_json({"ok": False, "error": "Hermes bridge unavailable"}, status=502)
        finally:
            connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve visualization files with explicit UTF-8 text headers.")
    parser.add_argument("port", nargs="?", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", default=".")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler = partial(Utf8StaticHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving visualization files on http://{args.bind}:{args.port}/ from {args.directory}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received, exiting.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
