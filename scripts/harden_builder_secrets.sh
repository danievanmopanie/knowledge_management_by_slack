#!/usr/bin/env bash
set -euo pipefail

# One-time, root-run bootstrap step. Locks down the deployment .env file so the
# unprivileged builder-worker.service account (default: "danie") cannot read
# secrets off disk, even if a shell-guard regex is bypassed by indirection
# (e.g. `python -c "print(open('.env').read())"`).
#
# This works because systemd's EnvironmentFile= directive is read by systemd
# itself (PID 1, running as root) BEFORE it drops privileges to the service's
# configured User=. The resulting environment variables are still injected
# into the spawned process normally; the service account never needs its own
# read access to the file on disk for that to keep working.
#
# See docs/BUILDER_ATLAS_AEON.md, "One-time secret-permission bootstrap", for
# why this does NOT fully solve SSH-key exposure (a service account that owns
# its own SSH key can always read it) and what to do instead.

APP_DIR="${1:-/opt/knowledge_management_by_slack}"
ENV_FILE="${APP_DIR}/.env"

if [[ $EUID -ne 0 ]]; then
  echo "Run this script as root (it changes ownership of ${ENV_FILE})." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "No .env file found at ${ENV_FILE}; nothing to harden." >&2
  exit 1
fi

chown root:root "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

echo "Locked down ${ENV_FILE} (root:root, 0600)."
echo "systemd's EnvironmentFile= will still inject its variables into"
echo "builder-worker.service and atlas-builder.service, but the service"
echo "account itself can no longer read the file directly on disk."
