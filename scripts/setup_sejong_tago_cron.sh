#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INTERVAL_MINUTES=5
ENV_FILE="${REPO_ROOT}/.env"
PYTHON_BIN="python3"
RUN_INITIAL=1
CRON_MARKER="gcoo-sejong-tago-cron"

usage() {
  cat <<'EOF'
Usage: scripts/setup_sejong_tago_cron.sh [options]

Options:
  --repo-root PATH          Repository root. Defaults to parent of this script.
  --env-file PATH           .env file containing OPEN_DATA_PORTAL_API_KEY or DATA_GO_KR_SERVICE_KEY.
  --interval-minutes N      Cron interval in minutes. Defaults to 5.
  --python PATH             Python executable for venv creation. Defaults to python3.
  --no-initial-run          Register cron without running one immediate collection.
  -h, --help                Show this help.

This script creates .venv, installs requirements.txt, runs one Sejong TAGO
collection by default, and registers an idempotent crontab entry for the current
Unix user.
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

cat <<EOF
Sejong TAGO cron is installed.

Interval: every ${INTERVAL_MINUTES} minute(s)
Repo: ${REPO_ROOT}
Env: ${ENV_FILE}
Log: ${LOG_FILE}
Processed outputs: ${PROCESSED_DIR}
Visualization outputs: ${VISUALIZATION_DIR}

Cron entry:
${CRON_LINE}

Useful commands:
  crontab -l | grep ${CRON_MARKER}
  tail -f ${LOG_FILE}
  ${VENV_PYTHON} ${REPO_ROOT}/src/collect_sejong_tago.py --env ${ENV_FILE} --skip-fetch
EOF
