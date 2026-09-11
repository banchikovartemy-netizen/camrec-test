#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/banchikovartemy-netizen/camrec-test/main"
APP_DIR="/opt/camrec"
DATA_DIR="/var/lib/camrec"
ARCHIVE_DIR="$DATA_DIR/archive"
LOG_DIR="/var/log/camrec"
SERVICE="/etc/systemd/system/camrec.service"
LAUNCHER="/usr/local/bin/camrec"
UNINSTALLER="/usr/local/bin/camrec-uninstall"
DESKTOP_FILE="/usr/share/applications/camrec.desktop"
DESKTOP_USER="${SUDO_USER:-}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Запусти установщик так: sudo ./install.sh"
  exit 1
fi

echo "=== CamRec Linux ==="

if command -v pacman >/dev/null 2>&1; then
  pacman -S --needed --noconfirm python tk ffmpeg v4l-utils alsa-utils mpv curl coreutils
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3 python3-tk ffmpeg v4l-utils alsa-utils mpv curl coreutils
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-tkinter ffmpeg v4l-utils alsa-utils mpv curl coreutils
else
  echo "Неизвестный дистрибутив. Нужны: Python 3 + Tk, FFmpeg, v4l-utils, alsa-utils, mpv, curl."
  exit 1
fi

systemctl disable --now camrec-test.service 2>/dev/null || true
rm -f /etc/systemd/system/camrec-test.service /usr/local/bin/camrec-test /usr/local/bin/camrec-test-uninstall
rm -rf /opt/camrec-test

if ! getent group camrec >/dev/null 2>&1; then groupadd --system camrec; fi
if ! id camrec >/dev/null 2>&1; then useradd --system --gid camrec --home-dir /nonexistent --shell /usr/bin/nologin camrec; fi
for g in video audio; do
  if getent group "$g" >/dev/null 2>&1; then usermod -a -G "$g" camrec || true; fi
done

mkdir -p "$APP_DIR" "$ARCHIVE_DIR" "$LOG_DIR"
curl -fsSL "$REPO_RAW/camrec_daemon.py" -o "$APP_DIR/camrec_daemon.py"
curl -fsSL "$REPO_RAW/camrec_gui.py" -o "$APP_DIR/camrec_gui.py"
curl -fsSL "$REPO_RAW/camrec_actions.py" -o "$APP_DIR/camrec_actions.py"
curl -fsSL "$REPO_RAW/camrec_preview.py" -o "$APP_DIR/camrec_preview.py"
curl -fsSL "$REPO_RAW/camrec.service" -o "$SERVICE"
chmod 0755 "$APP_DIR/camrec_daemon.py" "$APP_DIR/camrec_gui.py" "$APP_DIR/camrec_actions.py" "$APP_DIR/camrec_preview.py"
chmod 0644 "$SERVICE"

if [[ -d /var/lib/camrec-test/archive ]]; then
  cp -an /var/lib/camrec-test/archive/. "$ARCHIVE_DIR/" 2>/dev/null || true
fi

if [[ ! -f "$DATA_DIR/settings.json" ]]; then
  cat > "$DATA_DIR/settings.json" <<'JSON'
{
  "status": "stop",
  "source_type": "usb",
  "usb_device": "/dev/video0",
  "network_url": "",
  "resolution": "640x480",
  "framerate": 30,
  "retention_days": 7,
  "segment_minutes": 5,
  "audio_enabled": false,
  "usb_audio_device": "default",
  "autostart_enabled": false,
  "autostart_source": "usb",
  "preview_enabled": false,
  "saved_dir": ""
}
JSON
fi

if [[ ! -f "$DATA_DIR/credentials.json" ]]; then
  printf '{\n  "username": "",\n  "password": ""\n}\n' > "$DATA_DIR/credentials.json"
fi

chown -R camrec:camrec "$ARCHIVE_DIR" "$LOG_DIR"
chmod 2775 "$ARCHIVE_DIR" "$LOG_DIR"
if [[ -n "$DESKTOP_USER" && "$DESKTOP_USER" != "root" ]]; then
  usermod -a -G camrec "$DESKTOP_USER" || true
  chown "$DESKTOP_USER":camrec "$DATA_DIR" "$DATA_DIR/settings.json" "$DATA_DIR/credentials.json"
else
  chown camrec:camrec "$DATA_DIR" "$DATA_DIR/settings.json" "$DATA_DIR/credentials.json"
fi
chmod 2775 "$DATA_DIR"
chmod 0664 "$DATA_DIR/settings.json"
chmod 0660 "$DATA_DIR/credentials.json"

cat > "$LAUNCHER" <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/python3 /opt/camrec/camrec_gui.py "$@"
EOF
chmod 0755 "$LAUNCHER"

cat > "$UNINSTALLER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then echo "Запусти: sudo camrec-uninstall"; exit 1; fi
systemctl disable --now camrec.service 2>/dev/null || true
rm -f /etc/systemd/system/camrec.service /usr/share/applications/camrec.desktop
systemctl daemon-reload
rm -rf /opt/camrec
rm -f /usr/local/bin/camrec /usr/local/bin/camrec-uninstall
echo "CamRec удалён. Записи и настройки оставлены в /var/lib/camrec"
EOF
chmod 0755 "$UNINSTALLER"

cat > "$DESKTOP_FILE" <<'EOF'
[Desktop Entry]
Type=Application
Name=CamRec
Comment=Запись с веб-камеры и IP/RTSP-камеры
Exec=camrec
Terminal=false
Categories=AudioVideo;Video;
EOF
chmod 0644 "$DESKTOP_FILE"

systemctl daemon-reload
systemctl enable --now camrec.service

echo
echo "Готово. Запуск: camrec"
echo "Архив: /var/lib/camrec/archive"
echo "Кнопка «Сохранить» копирует выбранную/последнюю запись в указанную папку."
