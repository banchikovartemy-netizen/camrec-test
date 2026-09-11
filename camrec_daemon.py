#!/usr/bin/env python3
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

DATA_DIR = Path('/var/lib/camrec')
ARCHIVE_DIR = DATA_DIR / 'archive'
CONFIG_FILE = DATA_DIR / 'settings.json'
CREDS_FILE = DATA_DIR / 'credentials.json'
STATE_DIR = Path('/run/camrec')
STATE_FILE = STATE_DIR / 'state.json'
LOG_DIR = Path('/var/log/camrec')
LOG_FILE = LOG_DIR / 'camrec.log'

DEFAULT_CONFIG = {
    'status': 'stop',
    'source_type': 'usb',
    'usb_device': '/dev/video0',
    'network_url': '',
    'resolution': '640x480',
    'framerate': 30,
    'retention_days': 7,
    'segment_minutes': 5,
    'audio_enabled': False,
    'usb_audio_device': 'default',
    'autostart_enabled': False,
    'autostart_source': 'usb',
    'preview_enabled': False,
}

ALLOWED_RES = {'640x480', '1280x720', '1280x960', '1920x1080'}
running = True
ffmpeg_proc = None
active_signature = None
last_cleanup = 0.0
boot_autostart = False
boot_config_mtime = None


def ensure_dirs():
    for p in (DATA_DIR, ARCHIVE_DIR, STATE_DIR, LOG_DIR):
        p.mkdir(parents=True, exist_ok=True)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)],
    )


def safe_int(v, default, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def validate(raw):
    cfg = DEFAULT_CONFIG.copy()
    if not isinstance(raw, dict):
        return cfg
    cfg['status'] = raw.get('status') if raw.get('status') in ('play', 'stop') else 'stop'
    cfg['source_type'] = raw.get('source_type') if raw.get('source_type') in ('usb', 'network') else 'usb'
    usb = str(raw.get('usb_device', cfg['usb_device'])).strip()
    if usb.startswith('/dev/video') and '..' not in usb:
        cfg['usb_device'] = usb
    cfg['network_url'] = str(raw.get('network_url', '')).strip()
    res = str(raw.get('resolution', cfg['resolution']))
    cfg['resolution'] = res if res in ALLOWED_RES else DEFAULT_CONFIG['resolution']
    cfg['framerate'] = safe_int(raw.get('framerate'), 30, 1, 60)
    cfg['retention_days'] = safe_int(raw.get('retention_days'), 7, 1, 3650)
    cfg['segment_minutes'] = safe_int(raw.get('segment_minutes'), 5, 1, 240)
    cfg['audio_enabled'] = bool(raw.get('audio_enabled', False))
    cfg['usb_audio_device'] = str(raw.get('usb_audio_device', 'default')).strip() or 'default'
    cfg['autostart_enabled'] = bool(raw.get('autostart_enabled', False))
    cfg['autostart_source'] = raw.get('autostart_source') if raw.get('autostart_source') in ('usb', 'network') else 'usb'
    cfg['preview_enabled'] = bool(raw.get('preview_enabled', False))
    return cfg


def read_json(path, default):
    try:
        with path.open('r', encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else default.copy()
    except Exception:
        return default.copy()


def read_config():
    return validate(read_json(CONFIG_FILE, DEFAULT_CONFIG))


def read_credentials():
    data = read_json(CREDS_FILE, {'username': '', 'password': ''})
    return str(data.get('username', '')), str(data.get('password', ''))


def authenticated_url(raw_url):
    raw_url = raw_url.strip()
    if not raw_url:
        raise RuntimeError('Не указан URL сетевой камеры.')
    if not raw_url.lower().startswith(('rtsp://', 'http://', 'https://')):
        raise RuntimeError('Нужен RTSP/HTTP URL камеры.')
    username, password = read_credentials()
    if not username:
        return raw_url
    parts = urlsplit(raw_url)
    host = parts.hostname or ''
    port = f':{parts.port}' if parts.port else ''
    auth = quote(username, safe='')
    if password:
        auth += ':' + quote(password, safe='')
    return urlunsplit((parts.scheme, f'{auth}@{host}{port}', parts.path, parts.query, parts.fragment))


def write_state(state, message='', **extra):
    payload = {'state': state, 'message': message, 'pid': os.getpid(), 'updated_at': time.time()}
    payload.update(extra)
    tmp = STATE_FILE.with_suffix('.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def signature(cfg):
    username, _ = read_credentials()
    return (
        cfg['source_type'], cfg['usb_device'], cfg['network_url'], username,
        cfg['resolution'], cfg['framerate'], cfg['segment_minutes'],
        cfg['audio_enabled'], cfg['usb_audio_device'], cfg['preview_enabled'],
    )


def build_command(cfg):
    prefix = 'webcam' if cfg['source_type'] == 'usb' else 'camera'
    output = str(ARCHIVE_DIR / f'{prefix}_%Y-%m-%d_%H-%M-%S.mp4')
    seg = cfg['segment_minutes'] * 60
    fps = cfg['framerate']
    gop = max(2, fps * 2)

    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin']

    if cfg['source_type'] == 'usb':
        dev = Path(cfg['usb_device'])
        if not dev.exists():
            raise RuntimeError(f'Веб-камера не найдена: {dev}')
        cmd += [
            '-thread_queue_size', '512',
            '-f', 'v4l2', '-channel', '0', '-pixel_format', 'yuyv422',
            '-framerate', str(fps), '-video_size', cfg['resolution'], '-i', cfg['usb_device'],
        ]
        video_map = '0:v:0'
        audio_map = '1:a:0'
        if cfg['audio_enabled']:
            cmd += ['-thread_queue_size', '512', '-f', 'alsa', '-i', cfg['usb_audio_device']]
    else:
        url = authenticated_url(cfg['network_url'])
        if url.lower().startswith('rtsp://'):
            cmd += ['-rtsp_transport', 'tcp']
        cmd += ['-i', url]
        video_map = '0:v:0'
        audio_map = '0:a?'

    cmd += ['-map', video_map]
    if cfg['audio_enabled']:
        cmd += ['-map', audio_map]
    cmd += [
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30',
        '-pix_fmt', 'yuv420p', '-g', str(gop), '-keyint_min', str(gop), '-sc_threshold', '0',
    ]
    if cfg['audio_enabled']:
        cmd += ['-c:a', 'aac', '-b:a', '96k', '-ar', '48000', '-af', 'aresample=async=1:first_pts=0']
    else:
        cmd += ['-an']
    cmd += [
        '-f', 'segment', '-segment_time', str(seg), '-segment_format', 'mp4',
        '-segment_format_options', 'movflags=+faststart', '-reset_timestamps', '1', '-strftime', '1', output,
    ]

    if cfg.get('preview_enabled'):
        cmd += [
            '-map', video_map, '-an',
            '-vf', 'scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1', '-r', '10',
            '-c:v', 'mpeg2video', '-q:v', '8', '-g', '10', '-bf', '0',
            '-f', 'mpegts', 'udp://127.0.0.1:23001?pkt_size=1316',
        ]
    return cmd


def start_recording(cfg):
    global ffmpeg_proc, active_signature
    cmd = build_command(cfg)
    logging.info('Запуск записи: source=%s audio=%s res=%s fps=%s', cfg['source_type'], cfg['audio_enabled'], cfg['resolution'], cfg['framerate'])
    ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    active_signature = signature(cfg)
    write_state('starting', 'Запуск записи', source=cfg['source_type'], audio=cfg['audio_enabled'])
    time.sleep(0.8)
    if ffmpeg_proc.poll() is not None:
        err = ''
        try:
            err = ffmpeg_proc.stderr.read()[-2000:]
        except Exception:
            pass
        raise RuntimeError(err.strip() or f'FFmpeg завершился с кодом {ffmpeg_proc.returncode}')
    write_state('recording', 'Идёт запись', source=cfg['source_type'], audio=cfg['audio_enabled'])


def stop_recording(reason='Остановлено'):
    global ffmpeg_proc, active_signature
    proc = ffmpeg_proc
    ffmpeg_proc = None
    active_signature = None
    if proc and proc.poll() is None:
        logging.info('Остановка FFmpeg')
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
    write_state('stopped', reason)


def cleanup_old_files(retention_days):
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for p in ARCHIVE_DIR.glob('*.mp4'):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logging.info('Удалено старых файлов: %d', removed)


def handle_signal(signum, frame):
    global running
    running = False


def effective_status(cfg):
    global boot_autostart, boot_config_mtime
    if boot_autostart:
        try:
            current = CONFIG_FILE.stat().st_mtime_ns
        except OSError:
            current = None
        if current == boot_config_mtime:
            return 'play'
        boot_autostart = False
    return cfg['status']


def main():
    global running, last_cleanup, boot_autostart, boot_config_mtime, ffmpeg_proc, active_signature
    ensure_dirs()
    setup_logging()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    initial = read_config()
    if initial['autostart_enabled']:
        initial['source_type'] = initial['autostart_source']
        boot_autostart = True
        try:
            boot_config_mtime = CONFIG_FILE.stat().st_mtime_ns
        except OSError:
            boot_config_mtime = None
        logging.info('Автозапуск записи: %s', initial['source_type'])

    write_state('idle', 'CamRec запущен')

    while running:
        try:
            cfg = read_config()
            if boot_autostart:
                cfg['source_type'] = cfg['autostart_source']
            wanted = effective_status(cfg)

            if wanted == 'play':
                sig = signature(cfg)
                if ffmpeg_proc is None:
                    try:
                        start_recording(cfg)
                    except Exception as exc:
                        logging.error('Не удалось начать запись: %s', exc)
                        if ffmpeg_proc is not None and ffmpeg_proc.poll() is None:
                            try:
                                ffmpeg_proc.terminate()
                            except Exception:
                                pass
                        ffmpeg_proc = None
                        active_signature = None
                        write_state('error', str(exc), source=cfg['source_type'])
                        time.sleep(3)
                elif ffmpeg_proc.poll() is not None:
                    code = ffmpeg_proc.returncode
                    err = ''
                    try:
                        err = ffmpeg_proc.stderr.read()[-1500:]
                    except Exception:
                        pass
                    logging.error('FFmpeg завершился: %s %s', code, err)
                    ffmpeg_proc = None
                    active_signature = None
                    write_state('error', err.strip() or f'FFmpeg завершился: {code}')
                    time.sleep(2)
                elif sig != active_signature:
                    stop_recording('Применение новых настроек')
            elif ffmpeg_proc is not None:
                stop_recording('Запись остановлена пользователем')
            else:
                write_state('stopped', 'Запись остановлена')

            now = time.time()
            if now - last_cleanup > 300:
                cleanup_old_files(cfg['retention_days'])
                last_cleanup = now
        except Exception as exc:
            logging.exception('Ошибка главного цикла: %s', exc)
            write_state('error', str(exc))
        time.sleep(1)

    stop_recording('CamRec завершён')


if __name__ == '__main__':
    main()
