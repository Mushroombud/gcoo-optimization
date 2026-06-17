#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
VISUALIZATION_ROOT = REPO_ROOT / "outputs" / "visualizations"
LAB_ROOT = VISUALIZATION_ROOT / "hermes_lab_workspace"
HERMES_ROOT = REPO_ROOT.parent / "hermes-agent"
HERMES_VENV_PYTHON = Path("/usr/local/lib/hermes-agent/venv/bin/python")


def maybe_reexec_with_hermes_python() -> None:
    if os.environ.get("GCOO_HERMES_BRIDGE_NO_REEXEC"):
        return
    if not HERMES_VENV_PYTHON.exists():
        return
    current = Path(sys.executable)
    if current == HERMES_VENV_PYTHON:
        return
    os.environ["GCOO_HERMES_BRIDGE_REEXEC"] = "1"
    os.execv(str(HERMES_VENV_PYTHON), [str(HERMES_VENV_PYTHON), *sys.argv])


if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))


READ_SYSTEM_PROMPT = f"""
You are the agent inside the GCOO optimization visualization.
Answer in Korean by default.
You may read and search the project at {REPO_ROOT}.
Use tools to inspect real implementation files before answering model, data, or visualization questions.
Do not write, patch, delete, commit, or run modifying commands in read mode.
Keep explanations concise and grounded in file names, variables, equations, and data columns from this repository.
""".strip()

LAB_SYSTEM_PROMPT = f"""
You are the agent inside the GCOO experiment lab.
Answer in Korean by default.
Your writable workspace is {LAB_ROOT}.
The lab is an isolated clone of the optimization model page, model code, configuration, model outputs, visualization assets, and processed back data.
Freely modify model assumptions, variables, visualization HTML, copied data, or copied Python code inside the lab workspace when the user asks.
Do not modify files outside {LAB_ROOT}.
When you change a visualization, update the lab files so the iframe page can refresh immediately.
Prefer direct, visible changes over long explanations.
""".strip()


@dataclass
class AgentSession:
    mode: str
    session_id: str
    db_session_id: str
    workspace: Path
    agent: Any
    history: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


SESSIONS: dict[tuple[str, str], AgentSession] = {}
SESSIONS_LOCK = threading.Lock()
SESSION_DB: Any | None = None
SESSION_DB_LOCK = threading.Lock()


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def ensure_lab(quick: bool = True) -> None:
    from init_hermes_lab import init_lab

    init_lab(force=False, quick=quick)


def get_session_db() -> Any:
    global SESSION_DB
    with SESSION_DB_LOCK:
        if SESSION_DB is None:
            from hermes_state import SessionDB

            SESSION_DB = SessionDB()
        return SESSION_DB


def mode_prefix(mode: str) -> str:
    return f"gcoo-{mode}-"


def normalize_session_id(mode: str, session_id: str | None = None) -> tuple[str, str]:
    raw = str(session_id or "").strip()
    if raw.startswith(mode_prefix(mode)):
        return raw, raw[len(mode_prefix(mode)) :]
    if raw.startswith("gcoo-"):
        return raw, raw
    client_id = raw or f"{mode}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    return f"{mode_prefix(mode)}{client_id}", client_id


def register_toolsets() -> None:
    import toolsets

    toolsets.TOOLSETS.setdefault(
        "gcoo-read",
        {
            "description": "Read-only GCOO model inspection tools",
            "tools": ["read_file", "search_files"],
            "includes": [],
        },
    )
    toolsets.TOOLSETS.setdefault(
        "gcoo-lab",
        {
            "description": "Writable GCOO lab tools",
            "tools": ["read_file", "write_file", "patch", "search_files", "terminal", "process", "execute_code"],
            "includes": [],
        },
    )


def runtime_kwargs() -> dict[str, Any]:
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider

    config = load_config()
    model_cfg = config.get("model")
    default_model = ""
    config_provider = None
    if isinstance(model_cfg, dict):
        default_model = str(model_cfg.get("default") or "")
        config_provider = model_cfg.get("provider")
    elif isinstance(model_cfg, str):
        default_model = model_cfg

    kwargs: dict[str, Any] = {"model": default_model}
    runtime = resolve_runtime_provider(requested=config_provider)
    kwargs.update(
        {
            "provider": runtime.get("provider"),
            "api_mode": runtime.get("api_mode"),
            "base_url": runtime.get("base_url"),
            "api_key": runtime.get("api_key"),
            "command": runtime.get("command"),
            "args": list(runtime.get("args") or []),
        }
    )
    return kwargs


def conversation_for_ui(messages: list[dict[str, Any]], limit: int = 8) -> list[dict[str, str]]:
    visible: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content")
        if isinstance(content, str):
            text = content.strip()
        else:
            text = json.dumps(content, ensure_ascii=False)
        if not text:
            continue
        visible.append({"role": role, "content": text})
    return visible[-limit:]


def precreate_db_session(mode: str, db_session_id: str, workspace: Path) -> Any:
    db = get_session_db()
    runtime = runtime_kwargs()
    db.create_session(
        session_id=db_session_id,
        source="gcoo-web",
        model=runtime.get("model"),
        model_config={
            "mode": mode,
            "workspace": str(workspace),
            "surface": "gcoo-visualization",
        },
        cwd=str(workspace),
    )
    try:
        db.update_session_cwd(db_session_id, str(workspace))
    except Exception:
        pass
    return db


def make_agent_session(mode: str, session_id: str, history: list[dict[str, Any]] | None = None) -> AgentSession:
    register_toolsets()
    if mode == "lab":
        ensure_lab(quick=True)
        workspace = LAB_ROOT
        enabled_toolsets = ["gcoo-lab"]
        system_prompt = LAB_SYSTEM_PROMPT
    else:
        workspace = REPO_ROOT
        enabled_toolsets = ["gcoo-read"]
        system_prompt = READ_SYSTEM_PROMPT

    from run_agent import AIAgent
    from tools.terminal_tool import register_task_env_overrides

    hermes_session_id, client_session_id = normalize_session_id(mode, session_id)
    session_db = precreate_db_session(mode, hermes_session_id, workspace)
    os.environ["TERMINAL_CWD"] = str(workspace)
    register_task_env_overrides(hermes_session_id, {"cwd": str(workspace)})
    agent = AIAgent(
        **runtime_kwargs(),
        platform="gcoo-web",
        enabled_toolsets=enabled_toolsets,
        quiet_mode=True,
        session_id=hermes_session_id,
        skip_context_files=True,
        load_soul_identity=False,
        max_iterations=24 if mode == "lab" else 12,
        ephemeral_system_prompt=system_prompt,
        session_db=session_db,
    )
    return AgentSession(
        mode=mode,
        session_id=client_session_id,
        db_session_id=hermes_session_id,
        workspace=workspace,
        agent=agent,
        history=list(history or []),
    )


def get_agent_session(mode: str, session_id: str) -> AgentSession:
    db_session_id, client_session_id = normalize_session_id(mode, session_id)
    key = (mode, db_session_id)
    with SESSIONS_LOCK:
        session = SESSIONS.get(key)
        if session is None:
            history = []
            try:
                history = get_session_db().get_messages_as_conversation(db_session_id, include_ancestors=True)
            except Exception:
                history = []
            session = make_agent_session(mode, client_session_id, history=history)
            SESSIONS[key] = session
        return session


def restore_agent_session(mode: str, db_session_id: str) -> AgentSession:
    db_session_id, client_session_id = normalize_session_id(mode, db_session_id)
    if not db_session_id.startswith(mode_prefix(mode)):
        raise ValueError("mode mismatch")
    db = get_session_db()
    history = db.get_messages_as_conversation(db_session_id, include_ancestors=True)
    if not history:
        raise ValueError("session not found")
    try:
        db.reopen_session(db_session_id)
    except Exception:
        pass
    session = make_agent_session(mode, client_session_id, history=history)
    with SESSIONS_LOCK:
        SESSIONS[(mode, db_session_id)] = session
    return session


def list_gcoo_sessions(mode: str, limit: int = 12) -> list[dict[str, Any]]:
    prefix = mode_prefix(mode)
    rows = get_session_db().list_sessions_rich(
        source="gcoo-web",
        limit=max(50, limit * 4),
        min_message_count=1,
        order_by_last_active=True,
        include_children=False,
    )
    sessions = []
    for row in rows:
        sid = str(row.get("id") or "")
        if not sid.startswith(prefix):
            continue
        sessions.append(
            {
                "id": sid,
                "sessionId": sid,
                "clientSessionId": sid[len(prefix) :],
                "mode": mode,
                "title": row.get("title") or "",
                "preview": row.get("preview") or "",
                "messageCount": int(row.get("message_count") or 0),
                "startedAt": row.get("started_at"),
                "lastActive": row.get("last_active"),
            }
        )
        if len(sessions) >= limit:
            break
    return sessions


def sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def git_status() -> str:
    result = run(["git", "status", "--short"], cwd=LAB_ROOT)
    return result.stdout.strip()


def save_label(subject: str) -> str:
    if subject.startswith("Initialize agent lab workspace"):
        return "초기 상태"
    if subject.startswith("Save agent lab state "):
        return "세이브 " + subject.removeprefix("Save agent lab state ")
    return subject or "세이브"


def changed_file_count(sha: str) -> int:
    result = run(["git", "show", "--name-only", "--pretty=format:", "--no-renames", sha], cwd=LAB_ROOT)
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def lab_saves(limit: int = 30) -> dict[str, Any]:
    ensure_lab(quick=True)
    head_result = run(["git", "rev-parse", "HEAD"], cwd=LAB_ROOT)
    head = head_result.stdout.strip() if head_result.returncode == 0 else ""
    log_result = run(
        [
            "git",
            "log",
            f"-n{limit}",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%ct%x1f%ci%x1f%s",
        ],
        cwd=LAB_ROOT,
    )
    saves: list[dict[str, Any]] = []
    if log_result.returncode == 0 and log_result.stdout.strip():
        for line in log_result.stdout.splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) != 5:
                continue
            sha, short_sha, epoch, iso_time, subject = parts
            try:
                timestamp = int(epoch)
            except ValueError:
                timestamp = 0
            saves.append(
                {
                    "id": sha,
                    "shortId": short_sha,
                    "label": save_label(subject),
                    "timestamp": timestamp,
                    "time": iso_time,
                    "changedFileCount": changed_file_count(sha),
                    "current": bool(head and sha == head),
                }
            )
    status = git_status()
    return {
        "saves": saves,
        "current": head,
        "hasChanges": bool(status),
        "unsavedCount": len(status.splitlines()) if status else 0,
        "status": status,
    }


def create_lab_save(prefix: str = "세이브") -> dict[str, Any]:
    status = git_status()
    if not status:
        return {"saved": False, "message": "변경 없음"}
    run(["git", "add", "."], cwd=LAB_ROOT)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    result = run(["git", "commit", "-m", f"{prefix} {stamp}"], cwd=LAB_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "save failed")
    head = run(["git", "rev-parse", "HEAD"], cwd=LAB_ROOT)
    return {"saved": True, "message": "세이브됨", "saveId": head.stdout.strip(), "detail": result.stdout.strip()}


def validate_save_id(save_id: str) -> str:
    candidate = str(save_id or "").strip()
    if not candidate:
        raise ValueError("세이브를 선택하세요")
    result = run(["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"], cwd=LAB_ROOT)
    if result.returncode != 0:
        raise ValueError("선택한 세이브를 찾을 수 없습니다")
    return result.stdout.strip()


def restore_to_save(save_id: str) -> dict[str, Any]:
    ensure_lab(quick=True)
    target = validate_save_id(save_id)
    if git_status():
        create_lab_save("되돌리기 전 자동 세이브")
    head_result = run(["git", "rev-parse", "HEAD"], cwd=LAB_ROOT)
    head = head_result.stdout.strip() if head_result.returncode == 0 else ""
    if target == head:
        return {"ok": True, "message": "이미 선택한 세이브입니다", **lab_saves()}
    ancestor = run(["git", "merge-base", "--is-ancestor", target, "HEAD"], cwd=LAB_ROOT)
    if ancestor.returncode != 0:
        raise ValueError("현재 흐름에서 선택할 수 없는 세이브입니다")
    commit_list = run(["git", "rev-list", f"{target}..HEAD"], cwd=LAB_ROOT)
    commits = [line.strip() for line in commit_list.stdout.splitlines() if line.strip()]
    if not commits:
        return {"ok": True, "message": "이미 선택한 세이브입니다", **lab_saves()}
    for sha in commits:
        result = run(["git", "revert", "--no-edit", "--no-commit", sha], cwd=LAB_ROOT)
        if result.returncode != 0:
            run(["git", "revert", "--abort"], cwd=LAB_ROOT)
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "restore failed")
    diff = run(["git", "diff", "--cached", "--quiet"], cwd=LAB_ROOT)
    if diff.returncode == 0:
        run(["git", "reset", "--hard", "HEAD"], cwd=LAB_ROOT)
        return {"ok": True, "message": "이미 같은 상태입니다", **lab_saves()}
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    result = run(["git", "commit", "-m", f"선택 세이브로 돌아감 {stamp}"], cwd=LAB_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "restore failed")
    return {"ok": True, "message": "선택한 세이브로 돌아감", **lab_saves()}


class HermesBridgeHandler(SimpleHTTPRequestHandler):
    server_version = "GCOOHermesBridge/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(VISUALIZATION_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/health"):
            self.send_json({"ok": True, "lab": LAB_ROOT.exists()})
            return
        if self.path.startswith("/api/sessions"):
            self.handle_sessions_list()
            return
        if self.path.startswith("/api/lab/saves"):
            self.handle_lab_saves()
            return
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/chat":
            self.handle_chat()
        elif self.path == "/api/sessions/restore":
            self.handle_session_restore()
        elif self.path == "/api/lab/init":
            self.handle_lab_init()
        elif self.path == "/api/lab/save":
            self.handle_lab_save()
        elif self.path == "/api/lab/revert":
            self.handle_lab_revert()
        elif self.path == "/api/lab/restore":
            self.handle_lab_restore()
        else:
            self.send_json({"error": "unknown endpoint"}, status=404)

    def handle_sessions_list(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        mode = "lab" if (query.get("mode") or ["read"])[0] == "lab" else "read"
        try:
            limit = min(max(int((query.get("limit") or ["12"])[0]), 1), 50)
        except ValueError:
            limit = 12
        try:
            self.send_json({"ok": True, "sessions": list_gcoo_sessions(mode, limit=limit)})
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_session_restore(self) -> None:
        payload = self.read_json()
        mode = "lab" if payload.get("mode") == "lab" else "read"
        session_id = str(payload.get("sessionId") or payload.get("id") or "").strip()
        if not session_id:
            self.send_json({"error": "missing sessionId"}, status=400)
            return
        try:
            session = restore_agent_session(mode, session_id)
            self.send_json(
                {
                    "ok": True,
                    "message": "복원됨",
                    "sessionId": session.db_session_id,
                    "clientSessionId": session.session_id,
                    "messages": conversation_for_ui(session.history),
                }
            )
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=404)

    def handle_lab_init(self) -> None:
        payload = self.read_json()
        ensure_lab(quick=bool(payload.get("quick", True)))
        self.send_json({"ok": True, "message": "준비됨", "workspace": str(LAB_ROOT), **lab_saves()})

    def handle_lab_saves(self) -> None:
        try:
            self.send_json({"ok": True, **lab_saves()})
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_lab_save(self) -> None:
        try:
            save_result = create_lab_save()
            self.send_json({"ok": True, **save_result, **lab_saves()})
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_lab_restore(self) -> None:
        payload = self.read_json()
        try:
            self.send_json(restore_to_save(str(payload.get("saveId") or payload.get("id") or "")))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_lab_revert(self) -> None:
        ensure_lab(quick=True)
        count_result = run(["git", "rev-list", "--count", "HEAD"], cwd=LAB_ROOT)
        try:
            count = int(count_result.stdout.strip() or "0")
        except ValueError:
            count = 0
        if count <= 1:
            if git_status():
                run(["git", "restore", "."], cwd=LAB_ROOT)
                run(["git", "clean", "-fd"], cwd=LAB_ROOT)
                self.send_json({"ok": True, "message": "초기 상태", **lab_saves()})
                return
            self.send_json({"ok": True, "message": "되돌릴 세이브 없음", **lab_saves()})
            return
        if git_status():
            run(["git", "add", "."], cwd=LAB_ROOT)
            run(["git", "commit", "-m", "되돌리기 전 자동 세이브"], cwd=LAB_ROOT)
        result = run(["git", "revert", "--no-edit", "HEAD"], cwd=LAB_ROOT)
        if result.returncode != 0:
            run(["git", "revert", "--abort"], cwd=LAB_ROOT)
            self.send_json({"error": result.stderr.strip() or result.stdout.strip() or "revert failed"}, status=500)
            return
        self.send_json({"ok": True, "message": "이전 세이브로 돌아감", **lab_saves()})

    def handle_chat(self) -> None:
        payload = self.read_json()
        mode = "lab" if payload.get("mode") == "lab" else "read"
        message = str(payload.get("message") or "").strip()
        session_id = str(payload.get("sessionId") or uuid.uuid4())
        if not message:
            self.send_json({"error": "empty message"}, status=400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

        def worker() -> None:
            try:
                session = get_agent_session(mode, session_id)
                with session.lock:
                    events.put(("status", {"text": "에이전트 연결"}))

                    def on_delta(delta: str, **_kwargs: Any) -> None:
                        if delta:
                            events.put(("delta", {"text": delta}))

                    result = session.agent.run_conversation(
                        message,
                        system_message=LAB_SYSTEM_PROMPT if mode == "lab" else READ_SYSTEM_PROMPT,
                        conversation_history=session.history or None,
                        task_id=session.agent.session_id,
                        stream_callback=on_delta,
                    )
                    session.history = result.get("messages") or session.history
                    events.put(
                        (
                            "done",
                            {
                                "text": result.get("final_response") or "",
                                "sessionId": session.db_session_id,
                                "clientSessionId": session.session_id,
                            },
                        )
                    )
            except Exception as exc:
                events.put(("error", {"text": str(exc)}))
            finally:
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = events.get()
            if item is None:
                break
            event, data = item
            try:
                self.wfile.write(sse(event, data))
                self.wfile.flush()
            except BrokenPipeError:
                break


def main() -> None:
    maybe_reexec_with_hermes_python()
    parser = argparse.ArgumentParser(description="Serve GCOO visualization pages with the agent SSE bridge.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--init-lab", action="store_true", help="Initialize the lab workspace before serving.")
    args = parser.parse_args()
    if args.init_lab:
        ensure_lab(quick=False)
    server = ThreadingHTTPServer((args.host, args.port), HermesBridgeHandler)
    print(f"Agent bridge: http://{args.host}:{args.port}/")
    print(f"Visualizations: http://{args.host}:{args.port}/index.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
