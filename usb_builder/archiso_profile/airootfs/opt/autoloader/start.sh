#!/usr/bin/env bash
set -Eeuo pipefail

log_dir="/var/log/autoloader"
log_file="$log_dir/session.log"
mkdir -p "$log_dir"
exec > >(tee -a "$log_file") 2>&1

printf '\nLibre_Core AutoLoader startup: %s\n' "$(date --utc --iso-8601=seconds)"
printf 'Logs remain available with: journalctl -u autoloader.service\n'
printf 'Session log: %s\n' "$log_file"
printf 'Backup destinations must be separately mounted persistent volumes; live root storage is temporary.\n'

if [[ ! -x /opt/autoloader/venv/bin/python ]]; then
    printf 'ERROR: the image did not install the reviewed application wheel.\n' >&2
    exit 70
fi

cd /opt/autoloader
exec /opt/autoloader/venv/bin/python -m autoloader run
