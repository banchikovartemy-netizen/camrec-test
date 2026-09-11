# CamRec

Лёгкая программа для Linux для постоянной записи с USB-веб-камеры или IP/RTSP-камеры.

## Возможности

- USB-веб-камера (`/dev/video0`) и IP/RTSP-камера.
- Запись по сегментам MP4 с автоматическим удалением старых файлов.
- Опциональная запись звука.
  - Для веб-камеры/USB: выбранный ALSA-микрофон.
  - Для IP-камеры: аудиодорожка из RTSP-потока, если камера её отдаёт.
- Отдельное окно логина/пароля для IP-камеры.
- Live-просмотр.
- Просмотр записей через `mpv`.
- Автозапуск записи: можно выбрать веб-камеру или IP-камеру. Запись стартует после загрузки ПК и при следующем запуске интерфейса CamRec.

## Установка

```bash
wget -q https://raw.githubusercontent.com/banchikovartemy-netizen/camrec-test/main/install.sh
chmod +x install.sh
sudo ./install.sh
camrec
```

Записи находятся в:

```text
/var/lib/camrec/archive
```

Служба:

```bash
systemctl status camrec
```

Лог:

```bash
sudo tail -f /var/log/camrec/camrec.log
```

Удаление программы без удаления записей:

```bash
sudo camrec-uninstall
```
