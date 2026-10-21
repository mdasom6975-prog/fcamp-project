#!/usr/bin/env bash
# 시연용 VM(Ubuntu 22.04/24.04, x86_64 또는 ARM64) 1회 준비 스크립트.
# 저장소를 clone 한 뒤 그 안에서 실행한다:  bash deploy/install.sh
# 하는 일: apt 패키지 → Python venv + pip install → data/storage 폴더 → 스왑 2GB → cloudflared 설치.
# 여러 번 실행해도 안전하다(이미 된 단계는 건너뛴다). sudo 권한이 필요하다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # 저장소 루트 (이 스크립트의 상위 폴더)
BACKEND_DIR="$REPO_DIR/backend"
SWAP_SIZE="${SWAP_SIZE:-2G}"                      # 무료 티어(RAM 1GB)에서 pip·이미지 합성이 메모리 부족으로 죽지 않게

echo "== 1. apt 패키지 (python3-venv, git, sqlite3, curl)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip git sqlite3 curl ca-certificates

echo "== 2. Python 가상환경 + 의존성 ($BACKEND_DIR/.venv)"
if [ ! -x "$BACKEND_DIR/.venv/bin/python" ]; then
  python3 -m venv "$BACKEND_DIR/.venv"
fi
"$BACKEND_DIR/.venv/bin/pip" install --upgrade pip -q
"$BACKEND_DIR/.venv/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"

echo "== 3. 데이터·저장소 폴더"
mkdir -p "$BACKEND_DIR/data" "$BACKEND_DIR/storage"

echo "== 4. 스왑 $SWAP_SIZE"
if [ ! -f /swapfile ]; then
  sudo fallocate -l "$SWAP_SIZE" /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
fi
if ! sudo swapon --show | grep -q '^/swapfile'; then
  sudo swapon /swapfile
fi
if ! grep -q '^/swapfile' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null   # 재부팅 뒤에도 유지
fi

echo "== 5. cloudflared (공식 .deb)"
if ! command -v cloudflared >/dev/null 2>&1; then
  case "$(uname -m)" in
    x86_64)          CF_ARCH=amd64 ;;
    aarch64 | arm64) CF_ARCH=arm64 ;;
    *) echo "지원하지 않는 아키텍처: $(uname -m)"; exit 1 ;;
  esac
  TMP_DEB="$(mktemp /tmp/cloudflared.XXXXXX.deb)"
  curl -fsSL -o "$TMP_DEB" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}.deb"
  sudo dpkg -i "$TMP_DEB"
  rm -f "$TMP_DEB"
fi
cloudflared --version

cat <<MSG

준비 끝. 다음 단계 (docs/10_시연_배포.md 참고):
  1) $BACKEND_DIR/.env 작성 (API 키 4개, APP_PASSWORD, APP_PUBLIC=1 등)
  2) Mac 에서 'cd frontend && npm run build' 후 frontend/dist 를 $REPO_DIR/frontend/dist 로 scp
  3) 서비스 등록:
       sudo cp $REPO_DIR/deploy/brand-studio.service $REPO_DIR/deploy/cloudflared.service /etc/systemd/system/
       (두 파일 안의 /home/ubuntu/project 를 실제 경로 $REPO_DIR 로 고친다)
       sudo systemctl daemon-reload && sudo systemctl enable --now brand-studio cloudflared
  4) 공개 주소 확인: journalctl -u cloudflared --no-pager | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
MSG
