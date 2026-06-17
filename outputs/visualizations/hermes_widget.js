(function () {
  "use strict";

  if (window.__gcooHermesWidgetLoaded) {
    return;
  }
  window.__gcooHermesWidgetLoaded = true;

  const script = document.currentScript;
  const scriptUrl = script ? new URL(script.getAttribute("src") || "hermes_widget.js", window.location.href) : new URL("hermes_widget.js", window.location.href);
  const cssUrl = new URL("hermes_widget.css", scriptUrl);
  const configuredBridge = window.HERMES_BRIDGE_URL || localStorage.getItem("hermesBridgeUrl") || "";
  const sameOriginBridge = window.location.protocol.startsWith("http") && window.location.port === "8787" ? window.location.origin : "";
  const bridgeUrl = (configuredBridge || sameOriginBridge || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const mode = window.HERMES_AGENT_MODE || document.documentElement.dataset.hermesMode || document.body.dataset.hermesMode || "read";
  const isLab = mode === "lab";
  const isLabSidebar = isLab && (document.documentElement.dataset.hermesSidebar === "true" || document.body.dataset.hermesSidebar === "true");
  const isLabPreviewFrame = window.self !== window.top && scriptUrl.pathname.includes("/hermes_lab_workspace/");
  if (isLabPreviewFrame) {
    return;
  }

  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = cssUrl.href;
  document.head.appendChild(link);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  const icons = {
    bot: '<svg viewBox="0 0 24 24"><path d="M12 3v3"/><rect x="5" y="7" width="14" height="11" rx="4"/><path d="M9 12h.01M15 12h.01M8 18l-2 3M16 18l2 3"/></svg>',
    history: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v6h6M12 7v5l3 2"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 1-15.5 6.2"/><path d="M3 12a9 9 0 0 1 15.5-6.2"/><path d="M18 2v4h-4M6 22v-4h4"/></svg>',
    restore: '<svg viewBox="0 0 24 24"><path d="M3 7v6h6"/><path d="M3 13a8 8 0 1 0 2.3-5.7"/></svg>',
    save: '<svg viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
    send: '<svg viewBox="0 0 24 24"><path d="m22 2-7 20-4-9-9-4 20-7Z"/><path d="M22 2 11 13"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  };

  function icon(name) {
    const node = el("span", "hermes-icon");
    node.innerHTML = icons[name] || icons.bot;
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function iconButton(className, iconName, label) {
    const button = el("button", className);
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.append(icon(iconName), el("span", "hermes-sr-only", label));
    return button;
  }

  function appendMessage(log, role, text) {
    const item = el("div", `hermes-message ${role || ""}`.trim(), text);
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
    return item;
  }

  function sessionStorageKey() {
    return isLab ? "hermesLabSessionId" : "hermesReadSessionId";
  }

  function freshSessionId() {
    return `${mode}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function currentSessionId() {
    let sessionId = localStorage.getItem(sessionStorageKey());
    if (!sessionId) {
      sessionId = freshSessionId();
      localStorage.setItem(sessionStorageKey(), sessionId);
    }
    return sessionId;
  }

  function setCurrentSessionId(sessionId) {
    if (sessionId) {
      localStorage.setItem(sessionStorageKey(), sessionId);
    }
  }

  function eventLines(buffer) {
    const events = [];
    let index = buffer.indexOf("\n\n");
    while (index !== -1) {
      events.push(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
      index = buffer.indexOf("\n\n");
    }
    return { events, rest: buffer };
  }

  function parseSse(block) {
    const event = { type: "message", data: "" };
    block.split(/\r?\n/).forEach((line) => {
      if (line.startsWith("event:")) {
        event.type = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        event.data += line.slice(5).trimStart();
      }
    });
    try {
      event.json = event.data ? JSON.parse(event.data) : {};
    } catch (_err) {
      event.json = { text: event.data };
    }
    return event;
  }

  async function streamChat(message, log, sendButton, input, status) {
    appendMessage(log, "user", message);
    const assistant = appendMessage(log, "assistant", "");
    sendButton.disabled = true;
    status.textContent = "응답 중";

    const sessionId = currentSessionId();

    try {
      const response = await fetch(`${bridgeUrl}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, mode, sessionId, page: window.location.pathname }),
      });
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const parsed = eventLines(buffer);
        buffer = parsed.rest;
        parsed.events.map(parseSse).forEach((event) => {
          if (event.type === "delta") {
            assistant.textContent += event.json.text || "";
            log.scrollTop = log.scrollHeight;
          } else if (event.type === "status") {
            status.textContent = event.json.text || "처리 중";
          } else if (event.type === "error") {
            assistant.classList.add("error");
            assistant.textContent = event.json.text || "에이전트 연결 오류";
          } else if (event.type === "done") {
            if (!assistant.textContent.trim() && event.json.text) assistant.textContent = event.json.text;
            if (event.json.sessionId) setCurrentSessionId(event.json.sessionId);
            status.textContent = "연결됨";
            if (isLab) {
              window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
              window.dispatchEvent(new CustomEvent("hermes-lab-saves-refresh-needed"));
            }
          }
        });
      }
    } catch (error) {
      assistant.classList.add("error");
      assistant.textContent = "에이전트 서버 연결 필요";
      status.textContent = "연결 필요";
    } finally {
      sendButton.disabled = false;
      input.focus();
    }
  }

  function formatSaveTime(timestamp) {
    if (!timestamp) return "";
    return new Date(timestamp * 1000).toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function saveMeta(save) {
    const parts = [];
    const timeText = formatSaveTime(save.timestamp);
    if (timeText) parts.push(timeText);
    parts.push(`${save.changedFileCount || 0}개 파일`);
    if (save.shortId) parts.push(`코드 ${save.shortId}`);
    return parts.join(" · ");
  }

  function mountWidget() {
    const fab = iconButton("hermes-fab", "bot", "에이전트 열기");
    fab.type = "button";

    const panel = el("aside", "hermes-panel");
    if (isLabSidebar) {
      panel.classList.add("hermes-sidebar-panel", "open");
      document.body.classList.add("hermes-sidebar-ready");
    }
    panel.setAttribute("aria-label", "에이전트");
    const header = el("header");
    const brand = el("div", "hermes-brand");
    const mark = el("div", "hermes-agent-mark");
    mark.append(icon("bot"));
    const titleStack = el("div", "hermes-title-stack");
    const title = el("div", "hermes-title", isLab ? "실험실 에이전트" : "에이전트");
    const status = el("div", "hermes-status", "대기");
    titleStack.append(title, status);
    brand.append(mark, titleStack);
    const actions = el("div", "hermes-header-actions");
    const history = iconButton("hermes-icon-button", "history", "대화 기록");
    const fresh = iconButton("hermes-icon-button", "plus", "새 대화");
    const close = iconButton("hermes-icon-button hermes-close", "x", "닫기");
    actions.append(history, fresh);
    if (!isLabSidebar) actions.append(close);
    header.append(brand, actions);

    const sessionList = el("div", "hermes-session-list");
    sessionList.setAttribute("aria-label", "대화 기록");

    const log = el("div", "hermes-log");
    appendMessage(log, "system", isLab ? "바꾸고 싶은 내용을 바로 지시하세요." : "모델에 대해 바로 물어보세요.");

    const composer = el("form", "hermes-composer");
    const input = el("textarea", "hermes-input");
    input.rows = 2;
    input.placeholder = isLab ? "예: fleet을 650대로 바꾸고 결과를 다시 보여줘" : "예: 목적함수에서 C_i는 무슨 뜻이야?";
    const send = iconButton("hermes-send", "send", "보내기");
    send.type = "submit";
    composer.append(input, send);

    let savePanel = null;
    let loadSaves = null;
    if (isLabSidebar) {
      savePanel = el("section", "hermes-save-panel");
      const saveActions = el("div", "hermes-save-actions");
      const saveButton = el("button", "hermes-save-button primary");
      saveButton.type = "button";
      saveButton.append(icon("save"), el("span", "", "세이브"));
      const restoreButton = el("button", "hermes-save-button");
      restoreButton.type = "button";
      restoreButton.disabled = true;
      restoreButton.append(icon("restore"), el("span", "", "돌아가기"));
      const refreshButton = iconButton("hermes-icon-button", "refresh", "새로고침");
      const saveStatus = el("div", "hermes-save-status", "세이브 확인 중");
      const saveTimeline = el("div", "hermes-save-timeline");
      saveActions.append(saveButton, restoreButton, refreshButton);
      savePanel.append(saveActions, saveStatus, saveTimeline);

      let selectedSaveId = "";
      const setSaveBusy = (busy) => {
        [saveButton, restoreButton, refreshButton].forEach((button) => {
          button.disabled = busy || (button === restoreButton && button.dataset.canRestore !== "true");
        });
      };
      const renderSaves = (payload) => {
        const saves = payload.saves || [];
        const currentId = saves[0] ? saves[0].id : "";
        if (!selectedSaveId || !saves.some((save) => save.id === selectedSaveId)) {
          selectedSaveId = currentId;
        }
        saveTimeline.innerHTML = "";
        if (payload.hasChanges) {
          const unsaved = el("div", "hermes-save-item unsaved current");
          unsaved.append(
            el("span", "hermes-save-dot"),
            el("span", "hermes-save-copy"),
            el("span", "hermes-save-chip", "현재")
          );
          unsaved.querySelector(".hermes-save-copy").append(el("b", "", "저장 전 변경"), el("span", "", `${payload.unsavedCount || 0}개 파일`));
          saveTimeline.appendChild(unsaved);
        }
        if (!saves.length) {
          saveTimeline.appendChild(el("div", "hermes-session-empty", "세이브 없음"));
          saveStatus.textContent = "세이브 없음";
          restoreButton.dataset.canRestore = "false";
          restoreButton.disabled = true;
          return;
        }
        saves.forEach((save) => {
          const item = el("button", "hermes-save-item");
          item.type = "button";
          if (save.id === selectedSaveId) item.classList.add("selected");
          if (save.current) item.classList.add("current");
          const copy = el("span", "hermes-save-copy");
          copy.append(el("b", "", save.label || "세이브"), el("span", "", saveMeta(save)));
          item.append(el("span", "hermes-save-dot"), copy);
          if (save.current) item.append(el("span", "hermes-save-chip", "현재"));
          item.addEventListener("click", () => {
            selectedSaveId = save.id;
            renderSaves(payload);
          });
          saveTimeline.appendChild(item);
        });
        saveStatus.textContent = payload.hasChanges ? `${payload.unsavedCount || 0}개 변경 있음` : "최신 세이브 상태";
        restoreButton.dataset.canRestore = selectedSaveId && selectedSaveId !== currentId ? "true" : "false";
        restoreButton.disabled = restoreButton.dataset.canRestore !== "true";
      };
      loadSaves = async () => {
        saveStatus.textContent = "불러오는 중";
        try {
          const payload = await labGet("/api/lab/saves");
          renderSaves(payload);
        } catch (_error) {
          saveStatus.textContent = "에이전트 서버 연결 필요";
        }
      };
      saveButton.addEventListener("click", async () => {
        setSaveBusy(true);
        saveStatus.textContent = "세이브 중";
        try {
          const payload = await labRequest("/api/lab/save");
          renderSaves(payload);
          saveStatus.textContent = payload.message || "세이브됨";
        } catch (_error) {
          saveStatus.textContent = "세이브 실패";
        } finally {
          setSaveBusy(false);
          window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
        }
      });
      restoreButton.addEventListener("click", async () => {
        if (!selectedSaveId) return;
        setSaveBusy(true);
        saveStatus.textContent = "돌아가는 중";
        try {
          const payload = await labRequest("/api/lab/restore", { saveId: selectedSaveId });
          renderSaves(payload);
          saveStatus.textContent = payload.message || "돌아감";
          window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
        } catch (_error) {
          saveStatus.textContent = "돌아가기 실패";
        } finally {
          setSaveBusy(false);
        }
      });
      refreshButton.addEventListener("click", () => {
        window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
        loadSaves();
      });
      window.addEventListener("hermes-lab-saves-refresh-needed", loadSaves);
    }

    if (savePanel) {
      panel.append(header, savePanel, sessionList, log, composer);
    } else {
      panel.append(header, sessionList, log, composer);
    }
    if (isLabSidebar) {
      document.body.append(panel);
      window.setTimeout(() => loadSaves && loadSaves(), 250);
    } else {
      document.body.append(panel, fab);
    }

    const setSessionListOpen = (open) => {
      sessionList.classList.toggle("open", open);
      history.classList.toggle("active", open);
      history.setAttribute("aria-pressed", open ? "true" : "false");
    };

    const renderSessionList = (sessions) => {
      sessionList.innerHTML = "";
      const heading = el("div", "hermes-session-heading");
      heading.append(icon("history"), el("span", "", "대화 기록"));
      sessionList.appendChild(heading);
      if (!sessions.length) {
        sessionList.appendChild(el("div", "hermes-session-empty", "기록 없음"));
        return;
      }
      sessions.forEach((session) => {
        const item = el("button", "hermes-session-item");
        item.type = "button";
        const titleText = session.title || session.preview || session.sessionId;
        const metaText = `${session.messageCount || 0}개 메시지`;
        const copy = el("span", "hermes-session-copy");
        copy.append(el("b", "", titleText), el("span", "", metaText));
        item.append(icon("history"), copy);
        item.addEventListener("click", async () => {
          status.textContent = "복원 중";
          try {
            const response = await fetch(`${bridgeUrl}/api/sessions/restore`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ mode, sessionId: session.sessionId }),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || "restore failed");
            setCurrentSessionId(payload.sessionId);
            log.innerHTML = "";
            (payload.messages || []).forEach((message) => appendMessage(log, message.role, message.content));
            if (!(payload.messages || []).length) appendMessage(log, "system", "복원됨");
            setSessionListOpen(false);
            status.textContent = "복원됨";
            input.focus();
          } catch (_error) {
            status.textContent = "복원 실패";
          }
        });
        sessionList.appendChild(item);
      });
    };

    const loadSessions = async () => {
      setSessionListOpen(true);
      sessionList.innerHTML = "";
      const loading = el("div", "hermes-session-heading");
      loading.append(icon("history"), el("span", "", "불러오는 중"));
      sessionList.appendChild(loading);
      try {
        const response = await fetch(`${bridgeUrl}/api/sessions?mode=${encodeURIComponent(mode)}&limit=12`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "sessions failed");
        renderSessionList(payload.sessions || []);
      } catch (_error) {
        sessionList.innerHTML = "";
        sessionList.appendChild(el("div", "hermes-session-empty", "에이전트 서버 연결 필요"));
      }
    };

    const openPanel = () => {
      panel.classList.add("open");
      fab.setAttribute("aria-expanded", "true");
      input.focus();
    };
    const closePanel = () => {
      panel.classList.remove("open");
      fab.setAttribute("aria-expanded", "false");
    };

    fab.addEventListener("click", () => {
      if (panel.classList.contains("open")) closePanel();
      else openPanel();
    });
    close.addEventListener("click", closePanel);
    history.addEventListener("click", () => {
      if (sessionList.classList.contains("open")) {
        setSessionListOpen(false);
      } else {
        loadSessions();
      }
    });
    fresh.addEventListener("click", () => {
      setCurrentSessionId(freshSessionId());
      setSessionListOpen(false);
      log.innerHTML = "";
      appendMessage(log, "system", isLab ? "새 실험을 시작합니다." : "새 대화를 시작합니다.");
      status.textContent = "새 대화";
      input.focus();
    });
    composer.addEventListener("submit", (event) => {
      event.preventDefault();
      const message = input.value.trim();
      if (!message || send.disabled) return;
      input.value = "";
      streamChat(message, log, send, input, status);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        composer.requestSubmit();
      }
    });

    if (document.body.dataset.hermesAutoOpen === "true") {
      window.setTimeout(openPanel, 250);
    }
  }

  async function labRequest(path, options) {
    const response = await fetch(`${bridgeUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function labGet(path) {
    const response = await fetch(`${bridgeUrl}${path}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function mountLabControls() {
    const frame = document.querySelector("[data-hermes-lab-frame]");
    const status = document.querySelector("[data-hermes-lab-status]");
    const initButton = document.querySelector("[data-hermes-lab-init]");
    const saveButton = document.querySelector("[data-hermes-lab-save]");
    const revertButton = document.querySelector("[data-hermes-lab-revert]");
    const refreshButton = document.querySelector("[data-hermes-lab-refresh]");
    if (!frame || !status) return;

    const setBusy = (busy) => {
      [initButton, saveButton, revertButton, refreshButton].filter(Boolean).forEach((button) => {
        button.disabled = busy;
      });
    };
    const refreshFrame = () => {
      const src = frame.getAttribute("src").split("?")[0];
      frame.setAttribute("src", `${src}?t=${Date.now()}`);
    };
    const run = async (label, fn) => {
      setBusy(true);
      status.textContent = label;
      try {
        const result = await fn();
        status.textContent = result.message || "완료";
        refreshFrame();
        window.dispatchEvent(new CustomEvent("hermes-lab-saves-refresh-needed"));
      } catch (_error) {
        status.textContent = "에이전트 서버 연결 필요";
      } finally {
        setBusy(false);
      }
    };

    initButton && initButton.addEventListener("click", () => run("준비 중", () => labRequest("/api/lab/init")));
    saveButton && saveButton.addEventListener("click", () => run("저장 중", () => labRequest("/api/lab/save")));
    revertButton && revertButton.addEventListener("click", () => run("되돌리는 중", () => labRequest("/api/lab/revert")));
    refreshButton && refreshButton.addEventListener("click", refreshFrame);
    window.addEventListener("hermes-lab-refresh-needed", refreshFrame);

    run("준비 중", () => labRequest("/api/lab/init", { quick: true }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      mountWidget();
      mountLabControls();
    });
  } else {
    mountWidget();
    mountLabControls();
  }
})();
