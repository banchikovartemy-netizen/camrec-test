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

echo "=== CamRec: полная версия ==="

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

# Останавливаем старую тестовую службу, но не удаляем старый архив.
systemctl disable --now camrec-test.service 2>/dev/null || true
rm -f /etc/systemd/system/camrec-test.service /usr/local/bin/camrec-test /usr/local/bin/camrec-test-uninstall
rm -rf /opt/camrec-test

if ! getent group camrec >/dev/null 2>&1; then
  groupadd --system camrec
fi
if ! id camrec >/dev/null 2>&1; then
  useradd --system --gid camrec --home-dir /nonexistent --shell /usr/bin/nologin camrec
fi
for g in video audio; do
  if getent group "$g" >/dev/null 2>&1; then
    usermod -a -G "$g" camrec || true
  fi
done

mkdir -p "$APP_DIR" "$ARCHIVE_DIR" "$LOG_DIR" /usr/share/applications
curl -fsSL "$REPO_RAW/camrec_daemon.py" -o "$APP_DIR/camrec_daemon.py"
curl -fsSL "$REPO_RAW/camrec_gui.py" -o "$APP_DIR/camrec_gui.py"
curl -fsSL "$REPO_RAW/camrec.service" -o "$SERVICE"
chmod 0755 "$APP_DIR/camrec_daemon.py" "$APP_DIR/camrec_gui.py"
chmod 0644 "$SERVICE"

# Переносим старые тестовые записи в новый архив без перезаписи файлов.
if [[ -d /var/lib/camrec-test/archive ]]; then
  cp -an /var/lib/camrec-test/archive/. "$ARCHIVE_DIR/" 2>/dev/null || true
fi

# Сохраняем настройки тестовой версии при первом переходе на полную.
if [[ ! -f "$DATA_DIR/settings.json" && -f /var/lib/camrec-test/settings.json ]]; then
  cp /var/lib/camrec-test/settings.json "$DATA_DIR/settings.json"
fi
if [[ ! -f "$DATA_DIR/credentials.json" && -f /var/lib/camrec-test/credentials.json ]]; then
  cp /var/lib/camrec-test/credentials.json "$DATA_DIR/credentials.json"
fi

if [[ ! -f "$DATA_DIR/settings.json" ]]; then
  echo '{}' > "$DATA_DIR/settings.json"
fi
if [[ ! -f "$DATA_DIR/credentials.json" ]]; then
  echo '{"username":"","password":""}' > "$DATA_DIR/credentials.json"
fi

# Добавляем новые параметры, не ломая уже сохранённые настройки.
python3 - "$DATA_DIR/settings.json" <<'PY'
import json, sys
p = sys.argv[1]
defaults = {
    "status": "stop",
    "source_type": "usb",
    "usb_device": "/dev/video0",
    "network_url": "",
    "resolution": "640x480",
    "framerate": 30,
    "retention_days": 7,
    "segment_minutes": 5,
    "audio_enabled": False,
    "usb_audio_device": "default",
    "autostart_enabled": False,
    "autostart_source": "usb"
}
try:
    with open(p, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}
for k, v in defaults.items():
    data.setdefault(k, v)
data['status'] = 'stop'
with open(p, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY

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

cat > "$DESKTOP_FILE" <<'EOF'
[Desktop Entry]
Type=Application
Name=CamRec
Comment=Запись с веб-камеры и IP-камеры
Exec=/usr/local/bin/camrec
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=true
EOF
chmod 0644 "$DESKTOP_FILE"

cat > "$UNINSTALLER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo "Запусти: sudo camrec-uninstall"
  exit 1
fi
systemctl disable --now camrec.service 2>/dev/null || true
rm -f /etc/systemd/system/camrec.service /usr/share/applications/camrec.desktop
systemctl daemon-reload
rm -rf /opt/camrec
rm -f /usr/local/bin/camrec /usr/local/bin/camrec-uninstall
echo "CamRec удалён. Записи и настройки оставлены в /var/lib/camrec"
EOF
chmod 0755 "$UNINSTALLER"

systemctl daemon-reload
systemctl enable --now camrec.service

echo
echo "Готово. Запуск интерфейса:"
echo "  camrec"
echo "Или открой CamRec из меню приложений."
echo
echo "Записи:"
echo "  /var/lib/camrec/archive"
echo
echo "Старая тестовая папка /var/lib/camrec-test оставлена как резервная копия."
