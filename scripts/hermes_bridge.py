#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
VISUALIZATION_ROOT = REPO_ROOT / "outputs" / "visualizations"
LAB_ROOT = VISUALIZATION_ROOT / "hermes_lab_workspace"
RAW_DATA_ROOT = REPO_ROOT / "data" / "raw"
HERMES_ROOT = REPO_ROOT.parent / "hermes-agent"
HERMES_VENV_PYTHON = Path("/usr/local/lib/hermes-agent/venv/bin/python")
MEMORY_PATH = REPO_ROOT / "Memory.md"
LAB_FORBIDDEN_RELATIVE_PREFIXES = (Path("data/raw"),)
ORIGINAL_PROTECTED_PATHS = (
    RAW_DATA_ROOT,
    REPO_ROOT / "src",
    REPO_ROOT / "scripts",
    VISUALIZATION_ROOT / "optimization_model.html",
    VISUALIZATION_ROOT / "optimization_model_map.html",
    VISUALIZATION_ROOT / "optimization_model_data.json",
)
_LAB_TOOL_GUARDS_INSTALLED = False


def maybe_reexec_with_hermes_python() -> None:
    if os.environ.get("GCOO_HERMES_BRIDGE_NO_REEXEC"):
        return
    if os.environ.get("GCOO_HERMES_BRIDGE_REEXEC"):
        return
    if not HERMES_VENV_PYTHON.exists():
        return
    current = Path(sys.executable)
    if current.resolve() == HERMES_VENV_PYTHON.resolve():
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
Follow {MEMORY_PATH}: prioritize core implementation and generated model-summary files, especially src/visualize_optimization_model.py, before raw data dumps.
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
You may read original raw data under {RAW_DATA_ROOT} to inspect columns and data types, but never modify it.
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
SESSION_EVENT_SINKS: dict[str, Callable[[tuple[str, dict[str, Any]]], None]] = {}
SESSION_EVENT_SINKS_LOCK = threading.Lock()


def emit_session_event(session_id: str, event: str, data: dict[str, Any]) -> None:
    with SESSION_EVENT_SINKS_LOCK:
        sink = SESSION_EVENT_SINKS.get(session_id)
    if sink is None:
        return
    try:
        sink((event, data))
    except Exception:
        pass


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def tool_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def is_lab_task(task_id: Any) -> bool:
    return str(task_id or "").startswith(mode_prefix("lab"))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def is_raw_data_path(path: Path) -> bool:
    return is_within(path, RAW_DATA_ROOT)


def resolve_lab_path(path: str | None, allow_raw_read: bool = False) -> Path:
    raw = str(path or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if allow_raw_read and not candidate.is_absolute():
        parts = candidate.parts
        if len(parts) >= 2 and parts[0] == "data" and parts[1] == "raw":
            return (REPO_ROOT / candidate).resolve()
    if not candidate.is_absolute():
        candidate = LAB_ROOT / candidate
    return candidate.resolve()


def lab_path_guard(path: str | None, purpose: str = "path", allow_raw_read: bool = False) -> str | None:
    try:
        resolved = resolve_lab_path(path, allow_raw_read=allow_raw_read)
        lab_root = LAB_ROOT.resolve()
        if allow_raw_read and is_raw_data_path(resolved):
            return None
        if is_raw_data_path(resolved):
            return "Raw Data는 원본 단일 source-of-truth로 읽기만 허용됩니다."
        resolved.relative_to(lab_root)
    except (OSError, ValueError):
        return f"{purpose}는 실험실 폴더 안에서만 사용할 수 있습니다: {path}"
    try:
        relative = resolved.relative_to(LAB_ROOT.resolve())
    except ValueError:
        return f"{purpose}는 실험실 폴더 안에서만 사용할 수 있습니다: {path}"
    for prefix in LAB_FORBIDDEN_RELATIVE_PREFIXES:
        if relative == prefix or prefix in relative.parents:
            return "Raw Data는 원본 단일 source-of-truth로 읽기만 허용됩니다."
    return None


def guarded_patch_paths(patch_text: str | None) -> list[str]:
    if not patch_text:
        return []
    paths: list[str] = []
    pattern = r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+)$"
    for match in re.finditer(pattern, patch_text, re.MULTILINE):
        paths.append(match.group(1).strip())
    for match in re.finditer(r"^\*\*\*\s+Move\s+File:\s*(.+?)\s*->\s*(.+)$", patch_text, re.MULTILINE):
        paths.extend([match.group(1).strip(), match.group(2).strip()])
    return paths


def absolutize_lab_patch_paths(patch_text: str | None) -> str | None:
    if not patch_text:
        return patch_text

    def replace_file_header(match: re.Match[str]) -> str:
        return f"{match.group(1)}{resolve_lab_path(match.group(2).strip())}"

    text = re.sub(
        r"^(\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*)(.+)$",
        replace_file_header,
        patch_text,
        flags=re.MULTILINE,
    )

    def replace_move_header(match: re.Match[str]) -> str:
        old_path = resolve_lab_path(match.group(2).strip())
        new_path = resolve_lab_path(match.group(3).strip())
        return f"{match.group(1)}{old_path} -> {new_path}"

    return re.sub(
        r"^(\*\*\*\s+Move\s+File:\s*)(.+?)\s*->\s*(.+)$",
        replace_move_header,
        text,
        flags=re.MULTILINE,
    )


def terminal_command_guard(command: str, workdir: str | None) -> str | None:
    command_text = str(command or "")
    if not command_text.strip():
        return "실행할 명령이 비어 있습니다"

    workdir_error = lab_path_guard(workdir or ".", "workdir")
    if workdir_error:
        return workdir_error

    for protected in ORIGINAL_PROTECTED_PATHS:
        protected_text = str(protected.resolve())
        if protected_text in command_text:
            return f"원본 경로는 실험실 명령에서 수정할 수 없습니다: {protected_text}"

    if re.search(r"(^|[;&|]\s*)cd\s+(\.\.|/root/gcoo-optimization(?:\s|$|/))", command_text):
        return "실험실 밖으로 이동하는 명령은 사용할 수 없습니다"

    try:
        tokens = shlex.split(command_text)
    except ValueError:
        tokens = command_text.split()
    for token in tokens:
        if token in {"..", "../"} or token.startswith("../") or "/../" in token:
            return "상위 폴더로 벗어나는 경로는 사용할 수 없습니다"
        if token.startswith("/"):
            try:
                resolved = Path(token).expanduser().resolve()
            except OSError:
                continue
            if not is_within(resolved, LAB_ROOT):
                for protected in ORIGINAL_PROTECTED_PATHS:
                    if resolved == protected.resolve() or protected.resolve() in resolved.parents:
                        return f"원본 경로는 실험실 명령에서 수정할 수 없습니다: {resolved}"
    return None


def install_lab_tool_guards() -> None:
    global _LAB_TOOL_GUARDS_INSTALLED
    if _LAB_TOOL_GUARDS_INSTALLED:
        return
    import model_tools  # noqa: F401 - importing discovers built-in tools
    from tools.registry import registry

    def wrap_path_tool(tool_name: str, path_keys: tuple[str, ...]) -> None:
        entry = registry.get_entry(tool_name)
        if entry is None:
            return
        original_handler = entry.handler
        allow_raw_read = tool_name in {"read_file", "search_files"}

        def guarded(args: dict[str, Any], **kw: Any) -> Any:
            if is_lab_task(kw.get("task_id")):
                args = dict(args)
                for key in path_keys:
                    if key in args and args.get(key) is not None:
                        error = lab_path_guard(args.get(key), key, allow_raw_read=allow_raw_read)
                        if error:
                            return tool_error(error)
                        args[key] = str(resolve_lab_path(args.get(key), allow_raw_read=allow_raw_read))
                if tool_name == "patch" and args.get("mode", "replace") == "patch":
                    for patch_path in guarded_patch_paths(args.get("patch")):
                        error = lab_path_guard(patch_path, "patch path")
                        if error:
                            return tool_error(error)
                    args["patch"] = absolutize_lab_patch_paths(args.get("patch"))
                if tool_name == "search_files" and not args.get("path"):
                    args["path"] = str(LAB_ROOT.resolve())
            return original_handler(args, **kw)

        entry.handler = guarded

    wrap_path_tool("read_file", ("path",))
    wrap_path_tool("write_file", ("path",))
    wrap_path_tool("search_files", ("path",))
    wrap_path_tool("patch", ("path",))

    terminal_entry = registry.get_entry("terminal")
    if terminal_entry is not None:
        original_terminal = terminal_entry.handler

        def guarded_terminal(args: dict[str, Any], **kw: Any) -> Any:
            if is_lab_task(kw.get("task_id")):
                error = terminal_command_guard(str(args.get("command") or ""), args.get("workdir"))
                if error:
                    return tool_error(error)
                args = dict(args)
                args["workdir"] = str(resolve_lab_path(args.get("workdir") or "."))
            return original_terminal(args, **kw)

        terminal_entry.handler = guarded_terminal

    _LAB_TOOL_GUARDS_INSTALLED = True


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

    toolsets.TOOLSETS["gcoo-read"] = {
        "description": "Read-only GCOO model inspection tools",
        "tools": ["read_file", "search_files"],
        "includes": [],
    }
    toolsets.TOOLSETS["gcoo-lab"] = {
        "description": "Writable GCOO lab tools scoped to the isolated lab workspace",
        "tools": ["read_file", "write_file", "patch", "search_files", "terminal", "process"],
        "includes": [],
    }
    install_lab_tool_guards()


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


def compact_text(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    text = str(value).strip().replace("\n", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def tool_detail(name: str, preview: Any = None, args: Any = None) -> str:
    preview_text = compact_text(preview)
    if preview_text:
        return preview_text
    if isinstance(args, dict):
        for key in ("path", "file_path", "query", "pattern", "cmd", "command"):
            value = args.get(key)
            if value:
                return compact_text(value)
    return name


def tool_status_text(event_name: str, name: str, preview: Any = None, args: Any = None, is_error: bool = False) -> str:
    labels = {
        "read_file": ("파일 읽는 중", "파일 읽기 완료"),
        "search_files": ("파일 검색 중", "파일 검색 완료"),
        "terminal": ("명령 실행 중", "명령 실행 완료"),
        "process": ("프로세스 확인 중", "프로세스 확인 완료"),
        "execute_code": ("코드 실행 중", "코드 실행 완료"),
        "write_file": ("파일 쓰는 중", "파일 쓰기 완료"),
        "patch": ("패치 적용 중", "패치 적용 완료"),
    }
    started, completed = labels.get(name, (f"{name} 실행 중", f"{name} 완료"))
    if event_name in {"_thinking", "reasoning.available"} or name == "_thinking":
        return "근거 정리 중"
    if event_name == "tool.completed":
        return f"{completed}{' 오류' if is_error else ''}"
    detail = tool_detail(name, preview, args)
    return f"{started}: {detail}" if detail and detail != name else started


def make_agent_callbacks(session_id: str) -> dict[str, Callable[..., None]]:
    def tool_progress_callback(*args: Any, **kwargs: Any) -> None:
        event_name = str(args[0]) if args else str(kwargs.get("event") or "tool.started")
        name = str(args[1]) if len(args) > 1 else str(kwargs.get("tool_name") or kwargs.get("name") or "")
        preview = args[2] if len(args) > 2 else kwargs.get("preview")
        tool_args = args[3] if len(args) > 3 else kwargs.get("args")
        text = tool_status_text(event_name, name, preview, tool_args, bool(kwargs.get("is_error")))
        emit_session_event(session_id, "status", {"text": text, "kind": "tool", "tool": name, "toolEvent": event_name})

    def status_callback(*args: Any, **kwargs: Any) -> None:
        message = kwargs.get("message") or kwargs.get("text")
        kind = kwargs.get("kind")
        if len(args) == 1:
            message = args[0]
        elif len(args) >= 2:
            kind, message = args[0], args[1]
        text = compact_text(message)
        if str(kind or "") == "lifecycle" and ("caps context" in text or "auto-compaction" in text):
            return
        if text:
            emit_session_event(session_id, "status", {"text": text, "kind": str(kind or "status")})

    def event_callback(event_type: str, context: dict[str, Any] | None = None) -> None:
        if event_type == "session:compress":
            emit_session_event(session_id, "status", {"text": "이전 대화 정리 중", "kind": "session"})

    return {
        "tool_progress_callback": tool_progress_callback,
        "status_callback": status_callback,
        "event_callback": event_callback,
    }


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
        ephemeral_system_prompt=system_prompt,
        session_db=session_db,
        **make_agent_callbacks(hermes_session_id),
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
        with SESSIONS_LOCK:
            existing = SESSIONS.get(key)
            if existing is not None:
                return existing
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
    if subject.startswith("세이브 "):
        return subject
    if subject.startswith("되돌리기 전 자동 세이브"):
        return subject
    if subject.startswith("선택 세이브로 돌아감"):
        return subject
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
    ensure_lab(quick=True)
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
        self.send_header("Connection", "close")
        self.end_headers()

        events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        events.put(("status", {"text": "에이전트 준비 중"}))

        def worker() -> None:
            try:
                session = get_agent_session(mode, session_id)
                with session.lock:
                    def sink(item: tuple[str, dict[str, Any]]) -> None:
                        events.put(item)

                    with SESSION_EVENT_SINKS_LOCK:
                        SESSION_EVENT_SINKS[session.db_session_id] = sink
                    events.put(("status", {"text": "에이전트 연결"}))

                    def on_delta(delta: str, **_kwargs: Any) -> None:
                        if delta:
                            events.put(("delta", {"text": delta}))

                    try:
                        result = session.agent.run_conversation(
                            message,
                            system_message=LAB_SYSTEM_PROMPT if mode == "lab" else READ_SYSTEM_PROMPT,
                            conversation_history=session.history or None,
                            task_id=session.agent.session_id,
                            stream_callback=on_delta,
                        )
                    finally:
                        with SESSION_EVENT_SINKS_LOCK:
                            if SESSION_EVENT_SINKS.get(session.db_session_id) is sink:
                                del SESSION_EVENT_SINKS[session.db_session_id]
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
        heartbeat_index = 0
        heartbeat_messages = ("작업 계속 진행 중", "도구 결과 확인 중", "답변 준비 중")
        while True:
            try:
                item = events.get(timeout=6)
            except queue.Empty:
                item = ("status", {"text": heartbeat_messages[heartbeat_index % len(heartbeat_messages)], "kind": "heartbeat"})
                heartbeat_index += 1
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
