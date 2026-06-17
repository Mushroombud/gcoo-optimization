(function () {
  "use strict";

  if (window.__gcooHermesWidgetLoaded) {
    return;
  }
  window.__gcooHermesWidgetLoaded = true;

  const script = document.currentScript;
  const scriptUrl = script ? new URL(script.getAttribute("src") || "hermes_widget.js", window.location.href) : new URL("hermes_widget.js", window.location.href);
  const cssUrl = new URL("hermes_widget.css", scriptUrl);
  if (scriptUrl.search) cssUrl.search = scriptUrl.search;
  const sameOriginBridge = window.location.protocol.startsWith("http") ? window.location.origin : "";
  const configuredBridge = window.HERMES_BRIDGE_URL || "";
  const storedBridge = localStorage.getItem("hermesBridgeUrl") || "";
  const bridgeUrl = (configuredBridge || sameOriginBridge || storedBridge || "http://127.0.0.1:8787").replace(/\/+$/, "");
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

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function ensureMathJax() {
    if (window.MathJax && window.MathJax.typesetPromise) return Promise.resolve();
    if (window.__hermesMathJaxPromise) return window.__hermesMathJaxPromise;
    window.MathJax = window.MathJax || {
      tex: { inlineMath: [["\\(", "\\)"]], displayMath: [["\\[", "\\]"]] },
      svg: { fontCache: "global" },
    };
    window.__hermesMathJaxPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js";
      script.async = true;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
    return window.__hermesMathJaxPromise;
  }

  function scheduleMathTypeset(node) {
    if (!node.querySelector(".hermes-math, .hermes-math-block")) return;
    window.clearTimeout(node.__hermesMathTimer);
    node.__hermesMathTimer = window.setTimeout(() => {
      ensureMathJax()
        .then(() => window.MathJax && window.MathJax.typesetPromise ? window.MathJax.typesetPromise([node]) : null)
        .catch(() => {});
    }, 80);
  }

  function renderInlineMarkdown(text) {
    const slots = [];
    const slot = (html) => {
      const key = `\u0000${slots.length}\u0000`;
      slots.push(html);
      return key;
    };
    let source = String(text || "");
    source = source.replace(/`([^`\n]+)`/g, (_match, code) => slot(`<code>${escapeHtml(code)}</code>`));
    source = source.replace(/\\\((.+?)\\\)/g, (_match, math) => slot(`<span class="hermes-math">\\(${escapeHtml(math)}\\)</span>`));
    source = source.replace(/\$([^$\n]+?)\$/g, (_match, math) => slot(`<span class="hermes-math">\\(${escapeHtml(math)}\\)</span>`));
    let html = escapeHtml(source);
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>");
    slots.forEach((value, index) => {
      html = html.replaceAll(`\u0000${index}\u0000`, value);
    });
    return html;
  }

  function splitTableRow(row) {
    return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }

  function isTableSeparator(row) {
    return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(row.trim());
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let paragraph = [];
    let list = [];
    let listType = "";
    let quote = [];
    let table = [];
    let code = [];
    let math = [];
    let inCode = false;
    let inMath = false;

    function flushParagraph() {
      if (!paragraph.length) return;
      out.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }

    function flushList() {
      if (!list.length) return;
      const tag = listType === "ol" ? "ol" : "ul";
      out.push(`<${tag}>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${tag}>`);
      list = [];
      listType = "";
    }

    function flushQuote() {
      if (!quote.length) return;
      out.push(`<blockquote>${quote.map((line) => `<p>${renderInlineMarkdown(line)}</p>`).join("")}</blockquote>`);
      quote = [];
    }

    function flushTable() {
      if (!table.length) return;
      if (table.length < 2 || !isTableSeparator(table[1])) {
        paragraph.push(...table);
        table = [];
        return;
      }
      const headers = splitTableRow(table[0]);
      const rows = table.slice(2).map(splitTableRow);
      let html = '<div class="hermes-table-wrap"><table><thead><tr>';
      html += headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
      html += "</tr></thead><tbody>";
      html += rows.map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`).join("");
      html += "</tbody></table></div>";
      out.push(html);
      table = [];
    }

    for (const line of lines) {
      const trimmed = line.trim();

      if (trimmed.startsWith("```")) {
        if (inCode) {
          out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
          code = [];
          inCode = false;
        } else {
          flushTable(); flushQuote(); flushList(); flushParagraph();
          inCode = true;
        }
        continue;
      }
      if (inCode) {
        code.push(line);
        continue;
      }

      if (trimmed === "$$" || trimmed === "\\[" || trimmed === "\\]") {
        if (inMath) {
          out.push(`<div class="hermes-math-block">\\[${escapeHtml(math.join("\n"))}\\]</div>`);
          math = [];
          inMath = false;
        } else if (trimmed !== "\\]") {
          flushTable(); flushQuote(); flushList(); flushParagraph();
          inMath = true;
        }
        continue;
      }
      if (inMath) {
        math.push(line);
        continue;
      }
      const oneLineMath = /^\$\$(.+)\$\$$/.exec(trimmed);
      if (oneLineMath) {
        flushTable(); flushQuote(); flushList(); flushParagraph();
        out.push(`<div class="hermes-math-block">\\[${escapeHtml(oneLineMath[1].trim())}\\]</div>`);
        continue;
      }
      const bracketMath = /^\\\[(.+)\\\]$/.exec(trimmed);
      if (bracketMath) {
        flushTable(); flushQuote(); flushList(); flushParagraph();
        out.push(`<div class="hermes-math-block">\\[${escapeHtml(bracketMath[1].trim())}\\]</div>`);
        continue;
      }

      if (!trimmed) {
        flushTable(); flushQuote(); flushList(); flushParagraph();
        continue;
      }
      if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
        flushTable(); flushQuote(); flushList(); flushParagraph();
        out.push("<hr>");
        continue;
      }
      if (trimmed.includes("|")) {
        flushQuote(); flushList(); flushParagraph();
        table.push(line);
        continue;
      }
      const quoteMatch = /^>\s?(.*)$/.exec(trimmed);
      if (quoteMatch) {
        flushTable(); flushList(); flushParagraph();
        quote.push(quoteMatch[1]);
        continue;
      }
      const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
      if (heading) {
        flushTable(); flushQuote(); flushList(); flushParagraph();
        const level = Math.min(4, heading[1].length + 1);
        out.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        continue;
      }
      const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
      if (ordered) {
        flushTable(); flushQuote(); flushParagraph();
        if (listType && listType !== "ol") flushList();
        listType = "ol";
        list.push(ordered[1]);
        continue;
      }
      const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
      if (bullet) {
        flushTable(); flushQuote(); flushParagraph();
        if (listType && listType !== "ul") flushList();
        listType = "ul";
        list.push(bullet[1]);
        continue;
      }
      flushTable(); flushQuote(); flushList();
      paragraph.push(line);
    }

    if (inCode) out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
    if (inMath) out.push(`<div class="hermes-math-block">\\[${escapeHtml(math.join("\n"))}\\]</div>`);
    flushTable();
    flushQuote();
    flushList();
    flushParagraph();
    return out.join("");
  }

  function normalizeRole(role) {
    return String(role || "").trim().toLowerCase();
  }

  function clearWorkingState(item) {
    item.classList.remove("working");
    delete item.dataset.placeholder;
    item.removeAttribute("aria-live");
  }

  function setAssistantWorking(item, text) {
    const label = (text || "처리 중").trim() || "처리 중";
    item.dataset.rawText = label;
    item.dataset.placeholder = "status";
    item.classList.remove("rendered", "error");
    item.classList.add("working");
    item.setAttribute("aria-live", "polite");
    item.innerHTML = [
      '<span class="hermes-working-indicator">',
      '<span class="hermes-working-ring" aria-hidden="true"></span>',
      `<span class="hermes-working-copy">${escapeHtml(label)}</span>`,
      '<span class="hermes-working-dots" aria-hidden="true"><span></span><span></span><span></span></span>',
      "</span>",
    ].join("");
  }

  function setMessageContent(item, role, text) {
    const messageRole = normalizeRole(role);
    item.dataset.rawText = text || "";
    clearWorkingState(item);
    if (messageRole === "assistant") {
      item.classList.add("rendered");
      item.innerHTML = renderMarkdown(text || "");
      scheduleMathTypeset(item);
      return;
    }
    item.classList.remove("rendered");
    item.textContent = text || "";
  }

  function appendMessage(log, role, text) {
    const messageRole = normalizeRole(role);
    const item = el("div", `hermes-message ${messageRole || ""}`.trim());
    setMessageContent(item, messageRole, text || "");
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
    setAssistantWorking(assistant, "요청 보내는 중");
    sendButton.disabled = true;
    status.textContent = "응답 중";
    let assistantHasAnswer = false;
    let assistantText = "";
    let finished = false;

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

      function showAssistantStatus(text) {
        if (assistantHasAnswer) return;
        const next = (text || "처리 중").trim();
        if (!next) return;
        setAssistantWorking(assistant, next);
        log.scrollTop = log.scrollHeight;
      }

      function appendAssistantDelta(text) {
        if (!text) return;
        if (!assistantHasAnswer) {
          assistantHasAnswer = true;
          clearWorkingState(assistant);
        }
        assistantText += text;
        setMessageContent(assistant, "assistant", assistantText);
        log.scrollTop = log.scrollHeight;
      }

      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const parsed = eventLines(buffer);
        buffer = parsed.rest;
        parsed.events.map(parseSse).forEach((event) => {
          if (event.type === "delta") {
            appendAssistantDelta(event.json.text || "");
          } else if (event.type === "status") {
            status.textContent = event.json.text || "처리 중";
            showAssistantStatus(event.json.text || "처리 중");
          } else if (event.type === "error") {
            clearWorkingState(assistant);
            assistant.classList.add("error");
            assistant.textContent = event.json.text || "에이전트 연결 오류";
            assistantHasAnswer = true;
            assistantText = "";
          } else if (event.type === "done") {
            const finalText = event.json.text || "";
            if (finalText) {
              assistantText = finalText;
              setMessageContent(assistant, "assistant", assistantText);
              assistantHasAnswer = true;
            } else if (!assistantHasAnswer || assistant.dataset.placeholder === "status") {
              clearWorkingState(assistant);
              assistant.classList.add("error");
              assistant.textContent = "응답이 비어 있습니다";
              assistantHasAnswer = true;
            }
            if (event.json.sessionId) setCurrentSessionId(event.json.sessionId);
            status.textContent = "연결됨";
            finished = true;
            if (isLab) {
              window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
              window.dispatchEvent(new CustomEvent("hermes-lab-saves-refresh-needed"));
            }
          }
        });
        if (finished) {
          await reader.cancel().catch(() => {});
          break;
        }
      }
    } catch (error) {
      clearWorkingState(assistant);
      assistant.classList.add("error");
      assistant.textContent = "에이전트 서버 연결 필요";
      assistantHasAnswer = true;
      status.textContent = "연결 필요";
    } finally {
      if (assistantHasAnswer && !assistant.classList.contains("error") && status.textContent !== "연결됨") {
        status.textContent = "연결됨";
      }
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
    return parts.join(" · ");
  }

  function saveDisplayLabel(save, index, total) {
    const raw = save.label || "";
    if (raw === "초기 상태") return raw;
    if (raw.startsWith("되돌리기 전 자동 세이브")) return `자동 세이브 ${total - index}`;
    if (raw.startsWith("선택 세이브로 돌아감")) return `돌아간 상태 ${total - index}`;
    return `세이브 ${total - index}`;
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
      const historyButton = el("button", "hermes-save-button");
      historyButton.type = "button";
      historyButton.setAttribute("aria-expanded", "false");
      historyButton.append(icon("history"), el("span", "", "기록"));
      const saveButton = el("button", "hermes-save-button primary");
      saveButton.type = "button";
      saveButton.append(icon("save"), el("span", "", "저장"));
      const restoreButton = el("button", "hermes-save-button");
      restoreButton.type = "button";
      restoreButton.disabled = true;
      restoreButton.append(icon("restore"), el("span", "", "되돌리기"));
      const refreshButton = iconButton("hermes-icon-button", "refresh", "새로고침");
      const saveStatus = el("div", "hermes-save-status", "상태 확인 중");
      const saveTimeline = el("div", "hermes-save-timeline");
      saveActions.append(historyButton, saveButton, restoreButton, refreshButton);
      savePanel.append(saveActions, saveStatus, saveTimeline);

      let selectedSaveId = "";
      let saveTimelineOpen = false;
      const setSaveTimelineOpen = (open) => {
        saveTimelineOpen = open;
        saveTimeline.classList.toggle("open", open);
        historyButton.classList.toggle("active", open);
        historyButton.setAttribute("aria-expanded", open ? "true" : "false");
        restoreButton.disabled = !open || restoreButton.dataset.canRestore !== "true";
      };
      const setSaveBusy = (busy) => {
        [historyButton, saveButton, restoreButton, refreshButton].forEach((button) => {
          button.disabled = busy || (button === restoreButton && (!saveTimelineOpen || button.dataset.canRestore !== "true"));
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
          saveTimeline.appendChild(el("div", "hermes-session-empty", "저장 없음"));
          saveStatus.textContent = "저장 없음";
          restoreButton.dataset.canRestore = "false";
          restoreButton.disabled = true;
          return;
        }
        saves.forEach((save, index) => {
          const item = el("button", "hermes-save-item");
          item.type = "button";
          if (save.id === selectedSaveId) item.classList.add("selected");
          if (save.current) item.classList.add("current");
          const copy = el("span", "hermes-save-copy");
          copy.append(el("b", "", saveDisplayLabel(save, index, saves.length)), el("span", "", saveMeta(save)));
          item.append(el("span", "hermes-save-dot"), copy);
          item.append(el("span", "hermes-save-code", save.shortId || ""));
          if (save.current) item.append(el("span", "hermes-save-chip", "현재"));
          item.addEventListener("click", () => {
            selectedSaveId = save.id;
            renderSaves(payload);
          });
          saveTimeline.appendChild(item);
        });
        saveStatus.textContent = payload.hasChanges ? `${payload.unsavedCount || 0}개 변경 있음` : "저장됨";
        restoreButton.dataset.canRestore = selectedSaveId && selectedSaveId !== currentId ? "true" : "false";
        restoreButton.disabled = !saveTimelineOpen || restoreButton.dataset.canRestore !== "true";
      };
      loadSaves = async () => {
        saveStatus.textContent = "불러오는 중";
        try {
          const payload = await labGet("/api/lab/saves");
          renderSaves(payload);
        } catch (_error) {
          saveStatus.textContent = "상태를 불러올 수 없음";
        }
      };
      historyButton.addEventListener("click", () => {
        const nextOpen = !saveTimelineOpen;
        setSaveTimelineOpen(nextOpen);
        if (nextOpen) loadSaves();
      });
      saveButton.addEventListener("click", async () => {
        setSaveBusy(true);
        saveStatus.textContent = "저장 중";
        try {
          const payload = await labRequest("/api/lab/save");
          renderSaves(payload);
          saveStatus.textContent = payload.message || "저장됨";
        } catch (_error) {
          saveStatus.textContent = "저장 실패";
        } finally {
          setSaveBusy(false);
          window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
        }
      });
      restoreButton.addEventListener("click", async () => {
        if (!selectedSaveId) return;
        setSaveBusy(true);
        saveStatus.textContent = "되돌리는 중";
        try {
          const payload = await labRequest("/api/lab/restore", { saveId: selectedSaveId });
          renderSaves(payload);
          saveStatus.textContent = payload.message || "되돌림";
          window.dispatchEvent(new CustomEvent("hermes-lab-refresh-needed"));
        } catch (_error) {
          saveStatus.textContent = "되돌리기 실패";
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

    const restoreSession = async (sessionId, options) => {
      const opts = options || {};
      if (!sessionId) return false;
      if (!opts.silent) status.textContent = "복원 중";
      try {
        const response = await fetch(`${bridgeUrl}/api/sessions/restore`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, sessionId }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "restore failed");
        setCurrentSessionId(payload.sessionId);
        log.innerHTML = "";
        (payload.messages || []).forEach((message) => appendMessage(log, message.role, message.content));
        if (!(payload.messages || []).length && !opts.silent) appendMessage(log, "system", "복원됨");
        if (opts.closeList !== false) setSessionListOpen(false);
        status.textContent = opts.silent ? "연결됨" : "복원됨";
        input.focus();
        return true;
      } catch (_error) {
        if (!opts.silent) status.textContent = "복원 실패";
        return false;
      }
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
        item.addEventListener("click", () => restoreSession(session.sessionId));
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
        sessionList.appendChild(el("div", "hermes-session-empty", "기록을 불러올 수 없음"));
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

    window.setTimeout(() => {
      const storedSessionId = localStorage.getItem(sessionStorageKey());
      if (storedSessionId) restoreSession(storedSessionId, { silent: true, closeList: false });
    }, 150);
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
    const status = document.querySelector("[data-hermes-lab-status]") || { textContent: "" };
    const initButton = document.querySelector("[data-hermes-lab-init]");
    const saveButton = document.querySelector("[data-hermes-lab-save]");
    const revertButton = document.querySelector("[data-hermes-lab-revert]");
    const refreshButton = document.querySelector("[data-hermes-lab-refresh]");
    if (!frame) return;

    const normalizeLabFrameLinks = () => {
      try {
        const doc = frame.contentDocument;
        if (!doc) return;
        doc.querySelectorAll('a[href="./index.html"], a[href="index.html"]').forEach((link) => {
          link.setAttribute("href", "../index.html");
          link.setAttribute("target", "_top");
        });
      } catch (_error) {
        // The iframe is same-origin in normal lab use; ignore if a browser blocks access.
      }
    };
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
    frame.addEventListener("load", normalizeLabFrameLinks);
    window.addEventListener("hermes-lab-refresh-needed", refreshFrame);

    normalizeLabFrameLinks();
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
