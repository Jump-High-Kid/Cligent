#!/bin/bash
# Cligent 일일 백업 스크립트
# 실행: ~/Library/LaunchAgents/kr.cligent.backup.plist (일일 04:00)
#
# 백업 대상:
#   - data/cligent.db (SQLite, WAL-safe .backup 명령 사용)
#   - data/*.json, *.jsonl, *.txt
#   - prompts/ 전체
#   - .env (Fernet 키 포함)
#
# 암호화: openssl AES-256-CBC + PBKDF2 (macOS 내장)
# 비번: macOS Keychain의 'cligent-backup' 서비스에서 자동 조회
#
# 환경변수:
#   CLIGENT_BACKUP_DEST  백업 저장 경로 (default: ~/CligentBackups)

set -euo pipefail

# === 설정 ===
PROJECT_DIR="/Users/jhzmac/Projects/medical-assistant"
BACKUP_ROOT="${CLIGENT_BACKUP_DEST:-$HOME/CligentBackups}"
RETENTION_DAYS=30
KEYCHAIN_SERVICE="cligent-backup"

# === 준비 ===
DATE_ONLY=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
STAGING="$BACKUP_ROOT/staging"
ARCHIVE_DIR="$BACKUP_ROOT/archive"
LOG_FILE="$BACKUP_ROOT/backup.log"

mkdir -p "$STAGING" "$ARCHIVE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# 종료 시 staging 정리
trap 'rm -rf "$STAGING/cligent-$TIMESTAMP"' EXIT

log "=== Backup start ($TIMESTAMP) ==="

# === 1. Keychain 비번 확인 ===
PASSPHRASE=$(/usr/bin/security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null || echo "")
if [ -z "$PASSPHRASE" ]; then
  log "ERROR: Keychain에 '$KEYCHAIN_SERVICE' 비번이 없습니다."
  log "다음 명령으로 등록하세요:"
  log "  security add-generic-password -s '$KEYCHAIN_SERVICE' -a cligent -w 'YOUR_STRONG_PASSPHRASE'"
  exit 1
fi

# === 2. SQLite WAL-safe 백업 ===
WORK_DIR="$STAGING/cligent-$TIMESTAMP"
mkdir -p "$WORK_DIR/data" "$WORK_DIR/prompts"

if [ -f "$PROJECT_DIR/data/cligent.db" ]; then
  /usr/bin/sqlite3 "$PROJECT_DIR/data/cligent.db" ".backup '$WORK_DIR/data/cligent.db'"
  log "SQLite backup OK"
else
  log "WARN: cligent.db not found at $PROJECT_DIR/data/cligent.db"
fi

# === 3. 데이터 파일들 (있는 것만) ===
for f in blog_stats.json blog_texts.json blog_history.json agent_log.jsonl \
         feedback.jsonl feedback_ack.txt rbac_permissions.json survey.jsonl \
         feedback_report.md; do
  if [ -f "$PROJECT_DIR/data/$f" ]; then
    cp "$PROJECT_DIR/data/$f" "$WORK_DIR/data/"
  fi
done

# === 4. prompts 폴더 ===
if [ -d "$PROJECT_DIR/prompts" ]; then
  cp -R "$PROJECT_DIR/prompts/." "$WORK_DIR/prompts/"
fi

# === 5. .env (Fernet 키 포함) ===
if [ -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env" "$WORK_DIR/.env"
fi

# === 6. tarball ===
RAW_TAR="$STAGING/cligent-$DATE_ONLY.tar.gz"
tar -czf "$RAW_TAR" -C "$STAGING" "cligent-$TIMESTAMP"
log "Tarball: $(du -h "$RAW_TAR" | cut -f1)"

# === 7. 암호화 (AES-256-CBC, PBKDF2 100k iterations) ===
ENCRYPTED="$ARCHIVE_DIR/cligent-$DATE_ONLY.tar.gz.enc"
/usr/bin/openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
  -in "$RAW_TAR" -out "$ENCRYPTED" -pass "pass:$PASSPHRASE"
rm -f "$RAW_TAR"
log "Encrypted: $ENCRYPTED ($(du -h "$ENCRYPTED" | cut -f1))"

# === 8. 보관 기간 정리 ===
DELETED=$(find "$ARCHIVE_DIR" -name "cligent-*.tar.gz.enc" -type f -mtime +$RETENTION_DAYS -print -delete | wc -l | tr -d ' ')
if [ "$DELETED" -gt 0 ]; then
  log "Cleaned $DELETED old backups (>$RETENTION_DAYS days)"
fi

# === 9. 현재 백업 카운트 ===
COUNT=$(find "$ARCHIVE_DIR" -name "cligent-*.tar.gz.enc" -type f | wc -l | tr -d ' ')
log "Total backups in archive: $COUNT"

log "=== Backup complete ==="
