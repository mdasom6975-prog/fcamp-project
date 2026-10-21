#!/usr/bin/env bash
# SQLite DB(온라인 .backup)와 storage(로고·생성 이미지·ZIP)·data(호출 기록 등)를 백업 폴더에 담는다.
# 실행: bash deploy/backup.sh            → $BACKUP_DIR/app-<시각>.db, storage-<시각>.tgz, data-<시각>.tgz
# 변수는 환경변수로 바꿀 수 있다: BACKUP_DIR=/mnt/backup bash deploy/backup.sh
# 인스턴스를 지우기 전에 한 번 실행하고 scp 로 내려받는다:  scp -r ubuntu@<ip>:~/backups ./
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"            # 저장소 루트
BACKEND_DIR="${BACKEND_DIR:-$REPO_DIR/backend}"
DB_FILE="${DB_FILE:-$BACKEND_DIR/data/app.db}"            # SQLite 파일
STORAGE_DIR="${STORAGE_DIR:-$BACKEND_DIR/storage}"        # 업로드·생성 파일
DATA_DIR="${DATA_DIR:-$BACKEND_DIR/data}"                 # 호출 기록·일일 한도·프롬프트 로그 (DB 파일은 제외)
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"                 # 결과를 둘 폴더
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

if [ -f "$DB_FILE" ]; then
  # .backup 은 앱이 켜져 있어도 일관된 복사본을 만든다 (cp 와 달리 쓰기 중간 상태가 섞이지 않는다)
  sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/app-$STAMP.db'"
  echo "DB      → $BACKUP_DIR/app-$STAMP.db"
else
  echo "DB 파일이 없어 건너뜀: $DB_FILE"
fi

if [ -d "$STORAGE_DIR" ]; then
  tar -czf "$BACKUP_DIR/storage-$STAMP.tgz" -C "$(dirname "$STORAGE_DIR")" "$(basename "$STORAGE_DIR")"
  echo "storage → $BACKUP_DIR/storage-$STAMP.tgz"
fi

if [ -d "$DATA_DIR" ]; then
  tar -czf "$BACKUP_DIR/data-$STAMP.tgz" --exclude='*.db' --exclude='*.db-*' -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
  echo "data    → $BACKUP_DIR/data-$STAMP.tgz"
fi

du -sh "$BACKUP_DIR"
