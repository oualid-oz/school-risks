#!/bin/sh
# Postgres backups for the school-risks stack (runs in the `backup` compose service).
#
#   backup.sh            dump now, then every BACKUP_INTERVAL_SECONDS (default: daily)
#   backup.sh once       dump once and exit
#   backup.sh restore F  restore dump file F (stop the api first, see README)
#
# Connection settings come from PGHOST / PGUSER / PGPASSWORD / PGDATABASE.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

log() {
    printf '{"ts":"%s","level":"%s","logger":"backup","message":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2"
}

backup() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    final="$BACKUP_DIR/${PGDATABASE}_${stamp}.dump"
    partial="$BACKUP_DIR/.${PGDATABASE}_${stamp}.dump.partial"
    # Custom format: compressed, and pg_restore can restore single tables from it.
    if pg_dump --format=custom --no-owner --file "$partial"; then
        mv "$partial" "$final"
        log INFO "backup written: $(basename "$final") ($(du -h "$final" | cut -f1))"
    else
        rm -f "$partial"
        log ERROR "backup failed"
        return 1
    fi
    find "$BACKUP_DIR" -maxdepth 1 -name "${PGDATABASE}_*.dump" -mtime +"$BACKUP_KEEP_DAYS" |
        while read -r old; do
            rm -f "$old"
            log INFO "deleted old backup: $(basename "$old")"
        done
}

restore() {
    file="$1"
    [ -f "$file" ] || { log ERROR "no such file: $file"; exit 1; }
    pg_restore --list "$file" > /dev/null  # fail early on a corrupt file
    log INFO "restoring $(basename "$file") into $PGDATABASE"
    pg_restore --clean --if-exists --no-owner --single-transaction --dbname "$PGDATABASE" "$file"
    log INFO "restore finished"
}

case "${1:-loop}" in
    once) backup ;;
    restore) restore "${2:?usage: backup.sh restore FILE}" ;;
    loop)
        until pg_isready --quiet; do sleep 2; done
        while true; do
            backup || true  # keep the schedule even if one run fails
            sleep "$BACKUP_INTERVAL_SECONDS"
        done
        ;;
    *) echo "usage: backup.sh [once|restore FILE]" >&2; exit 2 ;;
esac
