#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/banchikovartemy-netizen/camrec-test/main"
APP_DIR="/opt/camrec-test"
DATA_DIR="/var/lib/camrec-test"
ARCHIVE_DIR="$DATA_DIR/archive"
LOG_DIR="/var/log/camrec-test"
SERVICE="/etc/systemd/system/camrec-test.service"
LAUNCHER="/usr/local/bin/camrec-test"
UNINSTALLER="/usr/local/bin/camrec-test-uninstall"
DESKTOP_USER="${SUDO_USER:-}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Запусти установщик так: sudo ./install.sh"
  exit 1
fi

echo "=== CamRec Test: установка на Arch Linux ==="

pacman -S --needed --noconfirm python tk ffmpeg v4l-utils coreutils curl

if ! getent group camrectest >/dev/null 2>&1; then
  groupadd --system camrectest
fi

if ! id camrectest >/dev/null 2>&1; then
  useradd --system --gid camrectest --home-dir /nonexistent --shell /usr/bin/nologin camrectest
fi

if getent group video >/dev/null 2>&1; then
  usermod -a -G video camrectest || true
fi

mkdir -p "$APP_DIR" "$ARCHIVE_DIR" "$LOG_DIR"

curl -fsSL "$REPO_RAW/cam_daemon.py" -o "$APP_DIR/cam_daemon.py"
curl -fsSL "$REPO_RAW/cam_gui.py" -o "$APP_DIR/cam_gui.py"
curl -fsSL "$REPO_RAW/camrec-test.service" -o "$SERVICE"
chmod 0755 "$APP_DIR/cam_daemon.py" "$APP_DIR/cam_gui.py"
chmod 0644 "$SERVICE"

cat > "$DATA_DIR/settings.json" <<'JSON'
{
  "status": "stop",
  "source_type": "usb",
  "usb_device": "/dev/video0",
  "network_url": "",
  "resolution": "640x480",
  "framerate": 30,
  "retention_days": 2,
  "segment_minutes": 2
}
JSON

if [[ ! -f "$DATA_DIR/credentials.json" ]]; then
  cat > "$DATA_DIR/credentials.json" <<'JSON'
{
  "username": "",
  "password": ""
}
JSON
fi

chown -R camrectest:camrectest "$ARCHIVE_DIR" "$LOG_DIR"
chmod 2775 "$ARCHIVE_DIR" "$LOG_DIR"

if [[ -n "$DESKTOP_USER" && "$DESKTOP_USER" != "root" ]]; then
  chown "$DESKTOP_USER":camrectest "$DATA_DIR" "$DATA_DIR/settings.json" "$DATA_DIR/credentials.json"
  chmod 2775 "$DATA_DIR"
  chmod 0664 "$DATA_DIR/settings.json"
  chmod 0660 "$DATA_DIR/credentials.json"
  usermod -a -G camrectest "$DESKTOP_USER" || true
else
  chown camrectest:camrectest "$DATA_DIR" "$DATA_DIR/settings.json" "$DATA_DIR/credentials.json"
  chmod 2775 "$DATA_DIR"
  chmod 0664 "$DATA_DIR/settings.json"
  chmod 0660 "$DATA_DIR/credentials.json"
fi

cat > "$LAUNCHER" <<'SH'
#!/usr/bin/env bash
exec /usr/bin/python3 /opt/camrec-test/cam_gui.py "$@"
SH
chmod 0755 "$LAUNCHER"

cat > "$UNINSTALLER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo "Запусти: sudo camrec-test-uninstall"
  exit 1
fi
systemctl disable --now camrec-test.service 2>/dev/null || true
rm -f /etc/systemd/system/camrec-test.service
systemctl daemon-reload
rm -rf /opt/camrec-test
rm -f /usr/local/bin/camrec-test /usr/local/bin/camrec-test-uninstall
echo "CamRec Test удалён. Архив и настройки оставлены в /var/lib/camrec-test"
SH
chmod 0755 "$UNINSTALLER"

systemctl daemon-reload
systemctl enable --now camrec-test.service

echo
echo "Готово. Запуск приложения:"
echo "  camrec-test"
echo
echo "Проверка службы:"
echo "  systemctl status camrec-test"
