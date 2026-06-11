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
  <title>Sejong TAGO PM Visualization</title>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; color: #1b1f24; }
    main { max-width: 840px; margin: 0 auto; padding: 48px 20px; }
    h1 { margin: 0 0 12px; font-size: 28px; }
    p { margin: 0 0 24px; color: #4b5563; line-height: 1.6; }
    nav { display: grid; gap: 12px; }
    a { display: block; padding: 16px 18px; border: 1px solid #d8dee8; border-radius: 8px; background: #fff; color: #0f4c81; text-decoration: none; font-weight: 650; }
    a span { display: block; margin-top: 4px; color: #6b7280; font-size: 14px; font-weight: 400; }
  </style>
</head>
<body>
  <main>
    <h1>Sejong TAGO PM Visualization</h1>
    <p>수집 cron이 갱신하는 최신 세종 PM 스냅샷 기반 정적 리포트입니다.</p>
    <nav>
      <a href="./sejong_map.html">지도 보기<span>최신 PM 위치, 열지도, 500m 격자 집계</span></a>
      <a href="./sejong_charts_dashboard.html">차트 대시보드 보기<span>공급자별 공급량, 시간 추세, 배터리 분포</span></a>
      <a href="./sejong_visualization_manifest.json">Manifest JSON<span>생성 시각과 입력 데이터 행 수</span></a>
    </nav>
  </main>
</body>
</html>
EOF
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
    nohup "${VENV_PYTHON}" -m http.server "${STATIC_PORT}" \
      --bind "${STATIC_BIND_HOST}" \
      --directory "${VISUALIZATION_DIR}" \
      >> "${STATIC_SERVER_LOG_FILE}" 2>&1 &
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
  start_static_server
  start_cloudflare_tunnel
}

if (( STATIC_SERVING == 1 )); then
  setup_static_serving
else
  write_visualization_index
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
Local static map: ${STATIC_BASE_URL}/sejong_map.html
Local static dashboard: ${STATIC_BASE_URL}/sejong_charts_dashboard.html
EOF_STATIC
)
  STATIC_CURL_LINE="  curl -I ${STATIC_BASE_URL}/sejong_map.html"
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
