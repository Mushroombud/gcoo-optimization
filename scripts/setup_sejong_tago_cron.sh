#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INTERVAL_MINUTES=5
ENV_FILE="${REPO_ROOT}/.env"
PYTHON_BIN="python3"
RUN_INITIAL=1
CRON_MARKER="gcoo-sejong-tago-cron"
STATIC_SERVING=1
STATIC_PORT=8080
STATIC_BIND_HOST="127.0.0.1"
CLOUDFLARED_BIN="cloudflared"
CLOUDFLARE_TUNNEL=1
CLOUDFLARE_TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"

usage() {
  cat <<'EOF'
Usage: scripts/setup_sejong_tago_cron.sh [options]

Options:
  --repo-root PATH          Repository root. Defaults to parent of this script.
  --env-file PATH           .env file containing OPEN_DATA_PORTAL_API_KEY or DATA_GO_KR_SERVICE_KEY.
  --interval-minutes N      Cron interval in minutes. Defaults to 5.
  --python PATH             Python executable for venv creation. Defaults to python3.
  --no-initial-run          Register cron without running one immediate collection.
  --static-port N           Local static HTTP server port for visualization HTML. Defaults to 8080.
  --static-bind-host HOST   Local static HTTP bind host. Defaults to 127.0.0.1.
  --cloudflared PATH        cloudflared executable. Defaults to cloudflared.
  --cloudflare-token TOKEN  Cloudflare Tunnel token. Defaults to CLOUDFLARE_TUNNEL_TOKEN.
  --no-cloudflare-tunnel    Start only the local static HTTP server.
  --no-static-serving       Skip static HTTP server/tunnel and only register the collector cron.
  -h, --help                Show this help.

This script creates .venv, installs requirements.txt, runs one Sejong TAGO
collection by default, and registers an idempotent crontab entry for the current
Unix user. It also serves outputs/visualizations with a local Python HTTP server
and exposes it through Cloudflare Tunnel unless --no-static-serving is passed.
If --cloudflare-token is omitted, cloudflared creates a temporary quick tunnel.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="$(cd "$2" && pwd)"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --interval-minutes)
      INTERVAL_MINUTES="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --no-initial-run)
      RUN_INITIAL=0
      shift
      ;;
    --static-port)
      STATIC_PORT="$2"
      shift 2
      ;;
    --static-bind-host)
      STATIC_BIND_HOST="$2"
      shift 2
      ;;
    --cloudflared)
      CLOUDFLARED_BIN="$2"
      shift 2
      ;;
    --cloudflare-token)
      CLOUDFLARE_TUNNEL_TOKEN="$2"
      shift 2
      ;;
    --no-static-serving)
      STATIC_SERVING=0
      shift
      ;;
    --no-cloudflare-tunnel)
      CLOUDFLARE_TUNNEL=0
      shift
      ;;
    --server-name|--nginx-site-name|--no-nginx-install)
      echo "$1 was removed; static serving now uses Cloudflare Tunnel instead of nginx." >&2
      usage >&2
      exit 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${INTERVAL_MINUTES}" in
  ''|*[!0-9]*)
    echo "--interval-minutes must be an integer." >&2
    exit 2
    ;;
esac
if (( INTERVAL_MINUTES < 1 || INTERVAL_MINUTES > 59 )); then
  echo "--interval-minutes must be between 1 and 59." >&2
  exit 2
fi
case "${STATIC_PORT}" in
  ''|*[!0-9]*)
    echo "--static-port must be an integer." >&2
    exit 2
    ;;
esac
if (( STATIC_PORT < 1 || STATIC_PORT > 65535 )); then
  echo "--static-port must be between 1 and 65535." >&2
  exit 2
fi
case "${STATIC_BIND_HOST}" in
  ''|*[!A-Za-z0-9.:-]*)
    echo "--static-bind-host may contain only letters, numbers, dot, colon, and hyphen." >&2
    exit 2
    ;;
esac

if [[ ! -f "${REPO_ROOT}/requirements.txt" ]]; then
  echo "requirements.txt not found under ${REPO_ROOT}" >&2
  exit 1
fi
if [[ ! -f "${REPO_ROOT}/src/collect_sejong_tago.py" ]]; then
  echo "src/collect_sejong_tago.py not found under ${REPO_ROOT}" >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Env file not found: ${ENV_FILE}" >&2
  echo "Create it with OPEN_DATA_PORTAL_API_KEY=... before running this setup." >&2
  exit 1
fi
if ! grep -Eq '^(OPEN_DATA_PORTAL_API_KEY|DATA_GO_KR_SERVICE_KEY)=' "${ENV_FILE}"; then
  echo "${ENV_FILE} must define OPEN_DATA_PORTAL_API_KEY or DATA_GO_KR_SERVICE_KEY." >&2
  exit 1
fi
if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab command not found. Install cron or register the command manually." >&2
  exit 1
fi

VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/sejong_tago_cron.log"
STATIC_SERVER_LOG_FILE="${LOG_DIR}/sejong_tago_static_server.log"
CLOUDFLARED_LOG_FILE="${LOG_DIR}/sejong_tago_cloudflared.log"
PROCESSED_DIR="${REPO_ROOT}/data/processed/sejong_tago"
VISUALIZATION_DIR="${REPO_ROOT}/outputs/visualizations"
LOCK_FILE="${REPO_ROOT}/data/raw/sejong_tago_collect.lock"
RUN_DIR="${REPO_ROOT}/.run"
STATIC_SERVER_PID_FILE="${RUN_DIR}/sejong_tago_static_server.pid"
CLOUDFLARED_PID_FILE="${RUN_DIR}/sejong_tago_cloudflared.pid"

mkdir -p "${LOG_DIR}" "${PROCESSED_DIR}" "${VISUALIZATION_DIR}" "${REPO_ROOT}/data/raw" "${RUN_DIR}"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -r "${REPO_ROOT}/requirements.txt"

sq() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}


write_visualization_index() {
  cat > "${VISUALIZATION_DIR}/index.html" <<EOF
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GCOO Mobility Visualizations</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172033;
      --muted: #5d687a;
      --line: #d9e0ea;
      --panel: #ffffff;
      --sejong: #6d5f15;
      --model: #0f766e;
      --lab: #8a2c0d;
      --lab-deep: #13213a;
      --lab-hot: #f97316;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f4f6f8;
      color: var(--ink);
    }
    main {
      max-width: 1080px;
      margin: 0 auto;
      padding: 48px 20px;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 30px;
      line-height: 1.2;
    }
    .lead {
      max-width: 720px;
      margin: 0 0 30px;
      color: var(--muted);
      line-height: 1.65;
    }
    section {
      margin-top: 26px;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 18px;
      line-height: 1.35;
    }
    nav {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    a {
      display: block;
      min-height: 104px;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--model);
      text-decoration: none;
      font-weight: 700;
      box-shadow: 0 8px 22px rgba(23, 32, 51, 0.05);
    }
    a:hover {
      border-color: #9fb3c8;
      transform: translateY(-1px);
    }
    a:focus-visible {
      outline: 3px solid rgba(20, 184, 166, 0.5);
      outline-offset: 3px;
    }
    a span {
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      font-weight: 400;
    }
    .sejong a { color: var(--sejong); }
    .model a { color: var(--model); }
    .lab a { color: var(--lab); }
    .lab nav {
      grid-template-columns: minmax(0, 1fr);
      max-width: 560px;
    }
    .lab .lab-cta {
      position: relative;
      min-height: 132px;
      padding: 22px 24px;
      border: 0;
      overflow: hidden;
      isolation: isolate;
      background:
        radial-gradient(circle at 88% 18%, rgba(249, 115, 22, 0.95), transparent 24%),
        linear-gradient(135deg, var(--lab-deep) 0%, #0f766e 55%, var(--lab-hot) 100%);
      color: #ffffff;
      box-shadow: 0 18px 36px rgba(15, 23, 42, 0.22), 0 0 0 1px rgba(255, 255, 255, 0.2) inset;
    }
    .lab .lab-cta::after {
      content: "";
      position: absolute;
      right: 20px;
      bottom: 18px;
      z-index: 0;
      color: rgba(255, 255, 255, 0.14);
      font-size: 42px;
      line-height: 1;
      font-weight: 900;
      letter-spacing: 0;
      pointer-events: none;
    }
    .lab .lab-cta:hover {
      border-color: transparent;
      transform: translateY(-2px);
      box-shadow: 0 22px 46px rgba(15, 23, 42, 0.28), 0 0 0 2px rgba(255, 255, 255, 0.28) inset;
    }
    .lab .lab-cta:focus-visible {
      outline: 4px solid rgba(249, 115, 22, 0.45);
      outline-offset: 4px;
    }
    .lab .lab-cta span,
    .lab .lab-cta {
      z-index: 1;
    }
    .lab .lab-cta span {
      max-width: 390px;
      color: rgba(255, 255, 255, 0.9);
    }
    .feature {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 14px;
      align-items: stretch;
    }
    .feature a {
      min-height: 160px;
      color: var(--model);
    }
    .formula {
      margin: 0;
      min-height: 160px;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0f172a;
      color: #e2e8f0;
      overflow-x: auto;
      line-height: 1.65;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
      box-shadow: 0 8px 22px rgba(23, 32, 51, 0.05);
    }
    @media (max-width: 680px) {
      main { padding: 34px 16px; }
      nav, .feature { grid-template-columns: 1fr; }
      a { min-height: auto; }
      .lab .lab-cta::after { font-size: 34px; }
    }
  </style>
</head>
<body>
  <main>
    <h1>GCOO Mobility Visualizations</h1>
    <p class="lead">수집된 PM 데이터를 지도, 차트, 최적화 모델 관점에서 함께 볼 수 있는 시각화 허브입니다.</p>

    <section class="model">
      <h2>최적화 모델 시각화</h2>
      <div class="feature">
        <a href="./optimization_model.html">Optimization Model 보기<span>Sejong TAGO 스냅샷을 500m grid의 공급량, 추정 수요, competition pressure, rebalancing 부담으로 변환해 모델과 최종 배치 결과를 보여줍니다.</span></a>
        <pre class="formula">Qᵢₛ(xᵢ) = min {
  Aᵢₛ · [1 - exp(-βxᵢ / (1 + θCᵢₛ))],
  Uxᵢ
}

maximize Eₛ[Σᵢ(pᵢ-v)Qᵢₛ(xᵢ) - Σᵢcᵢxᵢ - Σᵢrᵢ(xᵢ)]</pre>
      </div>
      <nav style="margin-top:12px;">
        <a href="./optimization_model_map.html">모델 지도 보기<span>Zone별 최적 배치량 x*, 기대 ride, zone별 profit을 지도에서 확인</span></a>
        <a href="./optimization_model_data.json">모델 데이터 JSON<span>최종 배치 결과, 파라미터, 생성된 산출물 경로</span></a>
        <a href="./model_sheet.html">Model Sheet 보기<span>Markdown 원문을 표, 코드블록, 수식 설명이 읽히는 문서 화면으로 렌더링</span></a>
      </nav>
    </section>

    <section class="lab">
      <h2>에이전트 실험실</h2>
      <nav>
        <a class="lab-cta" href="./hermes_lab.html" aria-label="에이전트 실험실 열기">실험실 열기<span>자연어로 모델, 변수, 시각화를 바꾸고 상태 저장과 되돌리기를 실행</span></a>
      </nav>
    </section>

    <section class="sejong">
      <h2>세종시 TAGO PM 시각화</h2>
      <nav>
        <a href="./sejong_map.html">세종 지도 보기<span>최신 PM 위치, 공급사별 기기, 지도 기반 히트맵</span></a>
        <a href="./sejong_charts_dashboard.html">세종 차트 대시보드 보기<span>공급사별 기기 수, 배터리, 시간대별 스냅샷</span></a>
        <a href="./sejong_visualization_manifest.json">세종 Manifest JSON<span>생성 산출물과 TAGO 수집 행 수 등</span></a>
      </nav>
    </section>
  </main>
  <script defer src="./hermes_widget.js"></script>
</body>
</html>
EOF
}

write_model_sheet_assets() {
  if [[ -f "${REPO_ROOT}/Data_Model_Sheet.md" ]]; then
    cp -p "${REPO_ROOT}/Data_Model_Sheet.md" "${VISUALIZATION_DIR}/Data_Model_Sheet.md"
  fi
}

pid_is_running() {
  local pid_file="$1"
  local expected="$2"

  if [[ ! -f "${pid_file}" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "${pid_file}")"
  if [[ -z "${pid}" ]] || ! kill -0 "${pid}" >/dev/null 2>&1; then
    return 1
  fi

  if [[ -n "${expected}" ]] && command -v ps >/dev/null 2>&1; then
    ps -p "${pid}" -o args= 2>/dev/null | grep -Fq "${expected}"
    return
  fi

  return 0
}

start_static_server() {
  if pid_is_running "${STATIC_SERVER_PID_FILE}" "${STATIC_PORT}"; then
    echo "Static HTTP server already running with pid $(cat "${STATIC_SERVER_PID_FILE}")."
  else
    echo "Starting static HTTP server on ${STATIC_BIND_HOST}:${STATIC_PORT}..."
    if command -v setsid >/dev/null 2>&1; then
      setsid "${VENV_PYTHON}" "${REPO_ROOT}/scripts/serve_visualizations.py" "${STATIC_PORT}" \
        --bind "${STATIC_BIND_HOST}" \
        --directory "${VISUALIZATION_DIR}" \
        >> "${STATIC_SERVER_LOG_FILE}" 2>&1 < /dev/null &
    else
      nohup "${VENV_PYTHON}" "${REPO_ROOT}/scripts/serve_visualizations.py" "${STATIC_PORT}" \
        --bind "${STATIC_BIND_HOST}" \
        --directory "${VISUALIZATION_DIR}" \
        >> "${STATIC_SERVER_LOG_FILE}" 2>&1 &
    fi
    printf "%s\n" "$!" > "${STATIC_SERVER_PID_FILE}"
  fi
}

start_cloudflare_tunnel() {
  if (( CLOUDFLARE_TUNNEL == 0 )); then
    return
  fi
  if ! command -v "${CLOUDFLARED_BIN}" >/dev/null 2>&1; then
    echo "cloudflared not found: ${CLOUDFLARED_BIN}" >&2
    echo "Install cloudflared, pass --cloudflared PATH, or pass --no-cloudflare-tunnel." >&2
    exit 1
  fi
  if pid_is_running "${CLOUDFLARED_PID_FILE}" "cloudflared"; then
    echo "Cloudflare Tunnel already running with pid $(cat "${CLOUDFLARED_PID_FILE}")."
    return
  fi

  if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
    echo "Starting Cloudflare Tunnel from token..."
    nohup "${CLOUDFLARED_BIN}" --no-autoupdate tunnel run --token "${CLOUDFLARE_TUNNEL_TOKEN}" \
      >> "${CLOUDFLARED_LOG_FILE}" 2>&1 &
  else
    echo "Starting temporary Cloudflare quick tunnel..."
    nohup "${CLOUDFLARED_BIN}" --no-autoupdate tunnel --url "http://${STATIC_BIND_HOST}:${STATIC_PORT}" \
      >> "${CLOUDFLARED_LOG_FILE}" 2>&1 &
  fi
  printf "%s\n" "$!" > "${CLOUDFLARED_PID_FILE}"
}

setup_static_serving() {
  write_visualization_index
  write_model_sheet_assets
  start_static_server
  start_cloudflare_tunnel
}

if (( STATIC_SERVING == 1 )); then
  setup_static_serving
else
  write_visualization_index
  write_model_sheet_assets
fi

COLLECT_CMD="cd $(sq "${REPO_ROOT}") && $(sq "${VENV_PYTHON}") $(sq "${REPO_ROOT}/src/collect_sejong_tago.py") --config $(sq "${REPO_ROOT}/config/model_config.yaml") --env $(sq "${ENV_FILE}") --processed-dir $(sq "${PROCESSED_DIR}") --visualization-dir $(sq "${VISUALIZATION_DIR}") --lock-file $(sq "${LOCK_FILE}") >> $(sq "${LOG_FILE}") 2>&1"
CRON_LINE="*/${INTERVAL_MINUTES} * * * * ${COLLECT_CMD} # ${CRON_MARKER}"

TMP_CRON="$(mktemp)"
trap 'rm -f "${TMP_CRON}"' EXIT

if crontab -l > "${TMP_CRON}" 2>/dev/null; then
  grep -v "${CRON_MARKER}" "${TMP_CRON}" > "${TMP_CRON}.new" || true
else
  : > "${TMP_CRON}.new"
fi
printf "%s\n" "${CRON_LINE}" >> "${TMP_CRON}.new"
crontab "${TMP_CRON}.new"

if (( RUN_INITIAL == 1 )); then
  echo "Running initial Sejong TAGO collection..."
  (
    cd "${REPO_ROOT}"
    "${VENV_PYTHON}" "${REPO_ROOT}/src/collect_sejong_tago.py" \
      --config "${REPO_ROOT}/config/model_config.yaml" \
      --env "${ENV_FILE}" \
      --processed-dir "${PROCESSED_DIR}" \
      --visualization-dir "${VISUALIZATION_DIR}" \
      --lock-file "${LOCK_FILE}"
  ) | tee -a "${LOG_FILE}"
fi

STATIC_BASE_URL="http://${STATIC_BIND_HOST}:${STATIC_PORT}"
STATIC_SERVING_SUMMARY="disabled"
STATIC_URL_LINES=""
STATIC_CURL_LINE=""
if (( STATIC_SERVING == 1 )); then
  STATIC_SERVING_SUMMARY="local HTTP server on ${STATIC_BASE_URL}"
  if (( CLOUDFLARE_TUNNEL == 1 )); then
    STATIC_SERVING_SUMMARY="${STATIC_SERVING_SUMMARY}, Cloudflare Tunnel log: ${CLOUDFLARED_LOG_FILE}"
  fi
  STATIC_URL_LINES=$(cat <<EOF_STATIC
Local static index: ${STATIC_BASE_URL}/
Local optimization model: ${STATIC_BASE_URL}/optimization_model.html
Local optimization map: ${STATIC_BASE_URL}/optimization_model_map.html
Local static map: ${STATIC_BASE_URL}/sejong_map.html
Local static dashboard: ${STATIC_BASE_URL}/sejong_charts_dashboard.html
EOF_STATIC
)
  STATIC_CURL_LINE="  curl -I ${STATIC_BASE_URL}/optimization_model.html"
  if (( CLOUDFLARE_TUNNEL == 1 )) && [[ -z "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
    STATIC_CURL_LINE="${STATIC_CURL_LINE}
  grep -Eo 'https://[^ ]+\.trycloudflare\.com' ${CLOUDFLARED_LOG_FILE} | tail -1"
  fi
fi

cat <<EOF
Sejong TAGO cron is installed.

Interval: every ${INTERVAL_MINUTES} minute(s)
Repo: ${REPO_ROOT}
Env: ${ENV_FILE}
Log: ${LOG_FILE}
Processed outputs: ${PROCESSED_DIR}
Visualization outputs: ${VISUALIZATION_DIR}
Static serving: ${STATIC_SERVING_SUMMARY}
${STATIC_URL_LINES}

Cron entry:
${CRON_LINE}

Useful commands:
  crontab -l | grep ${CRON_MARKER}
  tail -f ${LOG_FILE}
  ${VENV_PYTHON} ${REPO_ROOT}/src/collect_sejong_tago.py --env ${ENV_FILE} --skip-fetch
${STATIC_CURL_LINE}
EOF
