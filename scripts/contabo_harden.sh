#!/usr/bin/env bash
# Run as root on Contabo: bash scripts/contabo_harden.sh
# Revert exact captured state: bash scripts/contabo_harden.sh --revert
set -euo pipefail

BACKUP_DIR=/root/webos-audit-hardening.before-20260713
SSHD_DIR=/etc/ssh/sshd_config.d

require_root() {
    [ "${EUID}" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
}

backup_once() {
    [ -d "$BACKUP_DIR" ] && return
    install -d -m 700 "$BACKUP_DIR"
    cp -a "$SSHD_DIR" "$BACKUP_DIR/sshd_config.d"
    cp -a /opt/webos/env "$BACKUP_DIR/env"
    printf '%s\n' "$(stat -c '%a' /data)" > "$BACKUP_DIR/data.mode"
    printf '%s\n' "$(stat -c '%a' /data/app.db)" > "$BACKUP_DIR/app.db.mode"
    printf '%s\n' "$(stat -c '%a' /data/backups)" > "$BACKUP_DIR/backups.mode"
    if [ -e /data/backups/app-test.db.gz ]; then
        cp -a /data/backups/app-test.db.gz "$BACKUP_DIR/app-test.db.gz"
    fi
}

apply() {
    backup_once
    [ ! -e "$SSHD_DIR/00-hardening.conf" ] || { echo "00-hardening.conf already exists." >&2; exit 1; }
    [ -f "$SSHD_DIR/99-hardening.conf" ] || { echo "99-hardening.conf missing." >&2; exit 1; }

    # sshd uses first obtained value: this must sort before cloud-init's 50-* file.
    mv "$SSHD_DIR/99-hardening.conf" "$SSHD_DIR/00-hardening.conf"
    if ! sshd -t; then
        mv "$SSHD_DIR/00-hardening.conf" "$SSHD_DIR/99-hardening.conf"
        exit 1
    fi
    if ! systemctl reload ssh || ! sshd -T | grep -qi '^passwordauthentication no$'; then
        mv "$SSHD_DIR/00-hardening.conf" "$SSHD_DIR/99-hardening.conf"
        systemctl reload ssh || true
        exit 1
    fi
    sshd -T | grep -i '^passwordauthentication'

    chmod 750 /data
    chmod 600 /data/app.db
    chmod 700 /data/backups
    rm -f /data/backups/app-test.db.gz

    tmp=$(mktemp)
    grep -v '^WEBOS_PROD=' /opt/webos/env > "$tmp" || true
    printf 'WEBOS_PROD=1\n' >> "$tmp"
    install -o webos -g webos -m 600 "$tmp" /opt/webos/env
    rm -f "$tmp"
    systemctl restart webos
}

revert() {
    [ -d "$BACKUP_DIR" ] || { echo "Backup missing: $BACKUP_DIR" >&2; exit 1; }

    rm -f "$SSHD_DIR/00-hardening.conf" "$SSHD_DIR/99-hardening.conf"
    cp -a "$BACKUP_DIR/sshd_config.d/." "$SSHD_DIR/"
    sshd -t
    systemctl reload ssh

    chmod "$(cat "$BACKUP_DIR/data.mode")" /data
    chmod "$(cat "$BACKUP_DIR/app.db.mode")" /data/app.db
    chmod "$(cat "$BACKUP_DIR/backups.mode")" /data/backups
    cp -a "$BACKUP_DIR/env" /opt/webos/env
    if [ -e "$BACKUP_DIR/app-test.db.gz" ]; then
        cp -a "$BACKUP_DIR/app-test.db.gz" /data/backups/app-test.db.gz
    fi
    systemctl restart webos
}

require_root
case "${1:-}" in
    "") apply ;;
    --revert) revert ;;
    *) echo "Usage: $0 [--revert]" >&2; exit 2 ;;
esac
