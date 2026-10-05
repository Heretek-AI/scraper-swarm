#!/usr/bin/env bash
set -euo pipefail

# Scraper Swarm — One-Command Bootstrap Script
# Preflight checks, master key generation, and control plane launch

echo "========================================================"
echo "          SCRAPER SWARM BOOTSTRAP INSTALLER             "
echo "========================================================"

# 1. Preflight System Checks
echo "[+] Checking environment..."
command -v docker >/dev/null 2>&1 || { echo "[!] Docker is required but not installed."; exit 1; }
command -v docker compose >/dev/null 2>&1 || { echo "[!] Docker Compose is required."; exit 1; }

MEM_TOTAL_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
MEM_TOTAL_GB=$((MEM_TOTAL_KB / 1024 / 1024))
echo "[+] Detected RAM: ${MEM_TOTAL_GB} GB"
if [ "$MEM_TOTAL_GB" -lt 4 ]; then
    echo "[!] Warning: At least 4 GB RAM is recommended for running search/scraper engines."
fi

# 2. Setup Data Directory and Secure Permissions
DATA_DIR="${SWARM_DATA_DIR:-/var/lib/scraper-swarm}"
mkdir -p "${DATA_DIR}/env" "${DATA_DIR}/seccomp"
chmod 700 "${DATA_DIR}"

# 3. Generate Master Cryptographic Key if missing
MASTER_KEY_FILE="${DATA_DIR}/master.key"
if [ ! -f "${MASTER_KEY_FILE}" ]; then
    echo "[+] Generating AES-256 master key with 0600 permissions..."
    openssl rand -base64 32 > "${MASTER_KEY_FILE}"
    chmod 600 "${MASTER_KEY_FILE}"
fi

# 4. Generate One-Time Bootstrap Token
BOOTSTRAP_TOKEN="swarm_boot_$(openssl rand -hex 16)"
echo ""
echo "========================================================"
echo "   YOUR ONE-TIME BOOTSTRAP TOKEN (SAVE THIS NOW):       "
echo "   ${BOOTSTRAP_TOKEN}"
echo "========================================================"
echo ""

# 5. Persist bootstrap token securely for control plane startup
BOOT_ENV_FILE="${DATA_DIR}/env/bootstrap.env"
cat <<EOF > "${BOOT_ENV_FILE}"
SWARM_BOOTSTRAP_TOKEN=${BOOTSTRAP_TOKEN}
SWARM_DATA_DIR=${DATA_DIR}
EOF
chmod 600 "${BOOT_ENV_FILE}"

export SWARM_BOOTSTRAP_TOKEN="${BOOTSTRAP_TOKEN}"
export SWARM_DATA_DIR="${DATA_DIR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "[+] Starting Scraper Swarm control plane..."
if docker info >/dev/null 2>&1; then
    docker compose -f "${REPO_ROOT}/deploy/docker-compose.control.yml" up -d
    echo ""
    echo "[✓] Scraper Swarm Control Plane is running!"
    echo "[✓] Navigate to: https://localhost or http://localhost:80"
    echo "[✓] Enter your bootstrap token above to complete the Setup Wizard."
else
    echo "[!] Docker daemon not directly accessible without sudo. Start the stack manually with:"
    echo "    sudo docker compose -f ${REPO_ROOT}/deploy/docker-compose.control.yml up -d"
fi
