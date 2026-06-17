#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common import write_json  # noqa: E402
from visualize_optimization_model import (  # noqa: E402
    APPROVED_MODEL_PARAMETERS_FILE,
    PARAMETER_SEARCH_TRIALS,
    coerce_model_parameters,
    read_approved_model_parameters,
    run_parameter_search,
)


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
PARAMETER_SEARCH_PROGRESS_LOCK = threading.Lock()
PARAMETER_SEARCH_PROGRESS = {
    "ok": True,
    "status": "not_run",
    "phase": "idle",
    "message": "Parameter calibration simulation has not started.",
}
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
        if parsed.path == "/api/parameter-search-progress":
            self.send_json(parameter_search_progress_snapshot())
            return
        if parsed.path.startswith("/api/"):
            self.proxy_to_hermes_bridge("GET")
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/apply-best-constants":
            self.handle_apply_best_constants()
            return
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
            parameter_state = read_approved_model_parameters(output_dir)
            model_parameters = coerce_model_parameters(parameter_state.get("parameters"))
            update_parameter_search_progress(
                {
                    "ok": True,
                    "status": "running",
                    "phase": "queued",
                    "message": "Parameter calibration simulation request was accepted.",
                    "completed_parameter_combinations": 0,
                    "completed_cases": 0,
                    "progress_ratio": 0.0,
                    "progress_percent": 0.0,
                    "elapsed_seconds": 0.0,
                    "estimated_remaining_seconds": None,
                    "actual_cases_per_second": 0.0,
                    "started_at_epoch": time.time(),
                }
            )
            result = run_parameter_search(
                processed_dir,
                trial_count,
                lambda_step,
                beta_step,
                theta_step,
                allow_long_run=allow_long_run,
                max_workers=max_workers,
                progress_callback=update_parameter_search_progress,
                model_parameters=model_parameters,
            )
            write_json(output_dir / "parameter_search_results.json", result)
            update_parameter_search_progress(
                {
                    "ok": True,
                    "status": "complete" if result.get("ok") else "failed",
                    "phase": "complete" if result.get("ok") else "failed",
                    "message": "Parameter calibration simulation completed."
                    if result.get("ok")
                    else result.get("error", "Parameter calibration simulation did not complete."),
                    "completed_parameter_combinations": result.get("parameter_combination_count", 0),
                    "completed_cases": result.get("case_count", 0),
                    "remaining_parameter_combinations": 0,
                    "remaining_cases": 0,
                    "progress_ratio": 1.0 if result.get("ok") else 0.0,
                    "progress_percent": 100.0 if result.get("ok") else 0.0,
                    "finished_at_epoch": time.time(),
                    "best": result.get("best", {}),
                }
            )
            self.send_json(result, status=200)
        except BrokenPipeError:
            self.log_error("parameter search response client disconnected")
        except Exception as exc:  # noqa: BLE001
            self.log_error("parameter search failed: %s", exc)
            update_parameter_search_progress(
                {
                    "ok": False,
                    "status": "failed",
                    "phase": "failed",
                    "message": str(exc),
                    "finished_at_epoch": time.time(),
                }
            )
            self.send_json({"ok": False, "error": str(exc)}, status=500)
        finally:
            PARAMETER_SEARCH_LOCK.release()

    def handle_apply_best_constants(self) -> None:
        if PARAMETER_SEARCH_LOCK.locked():
            self.send_json(
                {"ok": False, "error": "Calibration simulation is still running. Apply after it completes."},
                status=409,
            )
            return

        output_dir = Path(getattr(self, "directory", REPO_ROOT / "outputs" / "visualizations")).resolve()
        results_path = output_dir / "parameter_search_results.json"
        constants_path = output_dir / APPROVED_MODEL_PARAMETERS_FILE
        try:
            try:
                request_payload = json.loads((self.read_body() or b"{}").decode("utf-8") or "{}")
            except json.JSONDecodeError:
                request_payload = {}

            best = request_payload.get("best") if isinstance(request_payload.get("best"), dict) else None
            source = "request_payload"
            if best is None:
                if not results_path.exists():
                    self.send_json(
                        {"ok": False, "error": "No parameter_search_results.json exists yet."},
                        status=400,
                    )
                    return
                results = json.loads(results_path.read_text(encoding="utf-8"))
                if not results.get("ok"):
                    self.send_json(
                        {"ok": False, "error": "Parameter search result is not a completed successful full-run."},
                        status=400,
                    )
                    return
                best = results.get("best")
                source = str(results_path)

            required = {"lambda_market", "beta_capture", "theta_competition"}
            if not isinstance(best, dict) or not required.issubset(best):
                self.send_json(
                    {"ok": False, "error": "Best result does not contain λ/β/θ constants."},
                    status=400,
                )
                return

            parameters = coerce_model_parameters(best)
            approved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            payload = {
                "ok": True,
                "status": "approved",
                "message": "5분 내로 재계산 시 반영됩니다.",
                "approved_at": approved_at,
                "approved_at_epoch": time.time(),
                "source": source,
                "source_results": str(results_path),
                "lambda_market": parameters["lambda_market"],
                "beta_capture": parameters["beta_capture"],
                "theta_competition": parameters["theta_competition"],
                "best_summary": {
                    key: best.get(key)
                    for key in [
                        "rank",
                        "avg_unmet_rides",
                        "total_unmet_rides",
                        "service_rate",
                        "shortage_zone_count",
                        "expected_rides",
                        "expected_profit_krw",
                    ]
                    if key in best
                },
            }
            write_json(constants_path, payload)
            self.send_json(
                {
                    "ok": True,
                    "message": "5분 내로 재계산 시 반영됩니다.",
                    "parameters": parameters,
                    "approved_at": approved_at,
                    "path": str(constants_path),
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.log_error("apply best constants failed: %s", exc)
            self.send_json({"ok": False, "error": str(exc)}, status=500)

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


def update_parameter_search_progress(update: dict) -> None:
    with PARAMETER_SEARCH_PROGRESS_LOCK:
        PARAMETER_SEARCH_PROGRESS.update(update)
        PARAMETER_SEARCH_PROGRESS["updated_at_epoch"] = time.time()


def parameter_search_progress_snapshot() -> dict:
    with PARAMETER_SEARCH_PROGRESS_LOCK:
        return dict(PARAMETER_SEARCH_PROGRESS)


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
