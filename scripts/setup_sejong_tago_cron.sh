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
SERVER_NAME="_"
NGINX_SITE_NAME="gcoo-sejong-tago"
INSTALL_NGINX=1

usage() {
  cat <<'EOF'
Usage: scripts/setup_sejong_tago_cron.sh [options]

Options:
  --repo-root PATH          Repository root. Defaults to parent of this script.
  --env-file PATH           .env file containing OPEN_DATA_PORTAL_API_KEY or DATA_GO_KR_SERVICE_KEY.
  --interval-minutes N      Cron interval in minutes. Defaults to 5.
  --python PATH             Python executable for venv creation. Defaults to python3.
  --no-initial-run          Register cron without running one immediate collection.
  --static-port N           Nginx static serving port for visualization HTML. Defaults to 8080.
  --server-name NAME        Nginx server_name. Defaults to _.
  --nginx-site-name NAME    Nginx site config name. Defaults to gcoo-sejong-tago.
  --no-static-serving       Skip nginx setup and only register the collector cron.
  --no-nginx-install        Do not install nginx automatically if it is missing.
  -h, --help                Show this help.

This script creates .venv, installs requirements.txt, runs one Sejong TAGO
collection by default, and registers an idempotent crontab entry for the current
Unix user. It also configures nginx to serve outputs/visualizations as static
HTML unless --no-static-serving is passed.
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
    --server-name)
      SERVER_NAME="$2"
      shift 2
      ;;
    --nginx-site-name)
      NGINX_SITE_NAME="$2"
      shift 2
      ;;
    --no-static-serving)
      STATIC_SERVING=0
      shift
      ;;
    --no-nginx-install)
      INSTALL_NGINX=0
      shift
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
case "${SERVER_NAME}" in
  ''|*[!A-Za-z0-9._*-]*)
    echo "--server-name may contain only letters, numbers, dot, underscore, hyphen, and wildcard." >&2
    exit 2
    ;;
esac
case "${NGINX_SITE_NAME}" in
  ''|*[!A-Za-z0-9._-]*)
    echo "--nginx-site-name may contain only letters, numbers, dot, underscore, and hyphen." >&2
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
PROCESSED_DIR="${REPO_ROOT}/data/processed/sejong_tago"
VISUALIZATION_DIR="${REPO_ROOT}/outputs/visualizations"
LOCK_FILE="${REPO_ROOT}/data/raw/sejong_tago_collect.lock"

mkdir -p "${LOG_DIR}" "${PROCESSED_DIR}" "${VISUALIZATION_DIR}" "${REPO_ROOT}/data/raw"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -r "${REPO_ROOT}/requirements.txt"

sq() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

nginx_quote() {
  printf '"%s"' "$(printf "%s" "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

sudo_run() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

ensure_sudo_available() {
  if [[ "${EUID}" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required for nginx setup. Re-run as root or pass --no-static-serving." >&2
    exit 1
  fi
}

ensure_nginx_available() {
  if command -v nginx >/dev/null 2>&1; then
    return
  fi
  if (( INSTALL_NGINX == 0 )); then
    echo "nginx is not installed. Install nginx or omit --no-nginx-install." >&2
    exit 1
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "nginx is not installed and automatic installation currently supports apt-get only." >&2
    echo "Install nginx manually or pass --no-static-serving." >&2
    exit 1
  fi

  echo "nginx not found; installing with apt-get..."
  sudo_run apt-get update
  sudo_run env DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
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

write_nginx_site_config() {
  local site_available="$1"
  local root_value
  root_value="$(nginx_quote "${VISUALIZATION_DIR}")"

  if [[ "${EUID}" -eq 0 ]]; then
    cat > "${site_available}" <<EOF
server {
    listen ${STATIC_PORT};
    server_name ${SERVER_NAME};

    root ${root_value};
    index index.html sejong_map.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \.(html|json|csv|js|css|png|jpg|jpeg|gif|svg|ico)$ {
        try_files \$uri =404;
        add_header Cache-Control "no-cache";
    }
}
EOF
  else
    cat <<EOF | sudo tee "${site_available}" >/dev/null
server {
    listen ${STATIC_PORT};
    server_name ${SERVER_NAME};

    root ${root_value};
    index index.html sejong_map.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \.(html|json|csv|js|css|png|jpg|jpeg|gif|svg|ico)$ {
        try_files \$uri =404;
        add_header Cache-Control "no-cache";
    }
}
EOF
  fi
}

reload_nginx() {
  sudo_run nginx -t
  if command -v systemctl >/dev/null 2>&1; then
    sudo_run systemctl enable nginx >/dev/null 2>&1 || true
    sudo_run systemctl reload nginx || sudo_run systemctl restart nginx
  elif command -v service >/dev/null 2>&1; then
    sudo_run service nginx reload || sudo_run service nginx restart
  else
    sudo_run nginx -s reload || sudo_run nginx
  fi
}

setup_static_serving() {
  local site_available
  local site_enabled

  ensure_sudo_available
  ensure_nginx_available
  write_visualization_index

  if [[ -d /etc/nginx/sites-available && -d /etc/nginx/sites-enabled ]]; then
    site_available="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
    site_enabled="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
    write_nginx_site_config "${site_available}"
    sudo_run ln -sf "${site_available}" "${site_enabled}"
  else
    sudo_run mkdir -p /etc/nginx/conf.d
    site_available="/etc/nginx/conf.d/${NGINX_SITE_NAME}.conf"
    write_nginx_site_config "${site_available}"
  fi

  reload_nginx
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

STATIC_HOST="${SERVER_NAME}"
if [[ "${STATIC_HOST}" == "_" ]]; then
  STATIC_HOST="<server-ip-or-domain>"
fi
STATIC_BASE_URL="http://${STATIC_HOST}:${STATIC_PORT}"
STATIC_SERVING_SUMMARY="disabled"
STATIC_URL_LINES=""
STATIC_CURL_LINE=""
if (( STATIC_SERVING == 1 )); then
  STATIC_SERVING_SUMMARY="nginx on ${STATIC_BASE_URL}"
  STATIC_URL_LINES=$(cat <<EOF_STATIC
Static index: ${STATIC_BASE_URL}/
Static map: ${STATIC_BASE_URL}/sejong_map.html
Static dashboard: ${STATIC_BASE_URL}/sejong_charts_dashboard.html
EOF_STATIC
)
  STATIC_CURL_LINE="  curl -I ${STATIC_BASE_URL}/sejong_map.html"
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
