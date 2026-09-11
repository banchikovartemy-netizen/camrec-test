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

DATA_DIR = Path("/var/lib/camrec-test")
VIDEO_DIR = DATA_DIR / "archive"
CONFIG_FILE = DATA_DIR / "settings.json"
CREDS_FILE = DATA_DIR / "credentials.json"
STATE_DIR = Path("/run/camrec-test")
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = Path("/var/log/camrec-test")
LOG_FILE = LOG_DIR / "camrec-test.log"

DEFAULT_CONFIG = {
    "status": "stop",
    "source_type": "usb",
    "usb_device": "/dev/video0",
    "network_url": "",
    "resolution": "640x480",
    "framerate": 30,
    "retention_days": 2,
    "segment_minutes": 2
}

ALLOWED_RES = {"640x480", "1280x720", "1280x960", "1920x1080"}

running = True
ffmpeg_proc = None
active_signature = None
last_cleanup = 0.0


def ensure_dirs():
    for p in (DATA_DIR, VIDEO_DIR, STATE_DIR, LOG_DIR):
        p.mkdir(parents=True, exist_ok=True)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ],
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
    cfg["status"] = raw.get("status") if raw.get("status") in ("play", "stop") else "stop"
    cfg["source_type"] = raw.get("source_type") if raw.get("source_type") in ("usb", "network") else "usb"

    usb = str(raw.get("usb_device", cfg["usb_device"])).strip()
    if usb.startswith("/dev/video") and ".." not in usb:
        cfg["usb_device"] = usb

    cfg["network_url"] = str(raw.get("network_url", "")).strip()
    res = str(raw.get("resolution", cfg["resolution"]))
    cfg["resolution"] = res if res in ALLOWED_RES else DEFAULT_CONFIG["resolution"]
    cfg["framerate"] = safe_int(raw.get("framerate"), 30, 1, 60)
    cfg["retention_days"] = safe_int(raw.get("retention_days"), 2, 1, 365)
    cfg["segment_minutes"] = safe_int(raw.get("segment_minutes"), 2, 1, 120)
    return cfg


def read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default.copy()
    except Exception:
        return default.copy()


def read_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            return validate(json.load(f))
    except Exception as exc:
        logging.error("Ошибка настроек: %s", exc)
        cfg = DEFAULT_CONFIG.copy()
        cfg["status"] = "stop"
        return cfg


def read_credentials():
    data = read_json(CREDS_FILE, {"username": "", "password": ""})
    return str(data.get("username", "")), str(data.get("password", ""))


def build_authenticated_url(raw_url):
    raw_url = raw_url.strip()
    if not raw_url:
        raise RuntimeError("Не указан URL сетевой камеры.")
    if not raw_url.lower().startswith(("rtsp://", "http://", "https://")):
        raise RuntimeError("Нужен полный RTSP/HTTP URL.")

    username, password = read_credentials()
    if not username:
        return raw_url

    parts = urlsplit(raw_url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    auth = quote(username, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    netloc = f"{auth}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def atomic_json(path, payload, mode=0o644):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_state(state, message="", **extra):
    payload = {
        "state": state,
        "message": message,
        "pid": os.getpid(),
        "updated_at": time.time(),
    }
    payload.update(extra)
    try:
        atomic_json(STATE_FILE, payload, 0o644)
    except Exception:
        pass


def signature(cfg):
    username, _ = read_credentials()
    return (
        cfg["source_type"], cfg["usb_device"], cfg["network_url"], username,
        cfg["resolution"], cfg["framerate"], cfg["segment_minutes"]
    )


def build_command(cfg):
    output = str(VIDEO_DIR / "test_%Y-%m-%d_%H-%M-%S.mp4")
    seg = cfg["segment_minutes"] * 60
    fps = cfg["framerate"]
    gop = max(2, fps * 2)

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin"]

    if cfg["source_type"] == "usb":
        dev = Path(cfg["usb_device"])
        if not dev.exists():
            raise RuntimeError(f"USB-камера не найдена: {dev}")
        cmd += [
            "-f", "v4l2",
            "-channel", "0",
            "-pixel_format", "yuyv422",
            "-framerate", str(fps),
            "-video_size", cfg["resolution"],
            "-i", cfg["usb_device"]
        ]
    else:
        url = build_authenticated_url(cfg["network_url"])
        if url.lower().startswith("rtsp://"):
            cmd += ["-rtsp_transport", "tcp"]
        cmd += ["-i", url, "-vf", f"scale={cfg['resolution']}"]

    cmd += [
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "30",
        "-pix_fmt", "yuv420p",
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-f", "segment",
        "-segment_time", str(seg),
        "-segment_format", "mp4",
        "-segment_format_options", "movflags=+faststart",
        "-reset_timestamps", "1",
        "-strftime", "1",
        output
    ]
    return cmd


def stop_ffmpeg():
    global ffmpeg_proc
    if not ffmpeg_proc:
        return
    if ffmpeg_proc.poll() is None:
        try:
            ffmpeg_proc.send_signal(signal.SIGINT)
            ffmpeg_proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
                ffmpeg_proc.wait(timeout=2)
        except ProcessLookupError:
            pass
    ffmpeg_proc = None


def start_ffmpeg(cfg):
    global ffmpeg_proc
    cmd = build_command(cfg)
    logging.info("Запуск FFmpeg: source=%s", cfg["source_type"])
    ffmpeg_proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, bufsize=1
    )
    time.sleep(1.0)
    if ffmpeg_proc.poll() is not None:
        err = ""
        try:
            err = ffmpeg_proc.stderr.read().strip()
        except Exception:
            pass
        code = ffmpeg_proc.returncode
        ffmpeg_proc = None
        raise RuntimeError(f"FFmpeg код {code}: {err[-900:] if err else 'без текста ошибки'}")


def cleanup(cfg):
    cutoff = time.time() - cfg["retention_days"] * 86400
    for p in VIDEO_DIR.glob("*.mp4"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def handle_signal(signum, frame):
    global running
    running = False


def main():
    global active_signature, last_cleanup, ffmpeg_proc
    ensure_dirs()
    setup_logging()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    write_state("starting", "Тестовый демон запускается")
    retry_after = 0

    try:
        while running:
            cfg = read_config()

            if time.time() - last_cleanup >= 3600:
                cleanup(cfg)
                last_cleanup = time.time()

            if cfg["status"] == "stop":
                stop_ffmpeg()
                active_signature = None
                write_state("stopped", "Запись остановлена")
                time.sleep(1)
                continue

            sig = signature(cfg)

            if ffmpeg_proc is not None and sig != active_signature:
                stop_ffmpeg()
                active_signature = None

            if ffmpeg_proc is not None and ffmpeg_proc.poll() is not None:
                code = ffmpeg_proc.returncode
                err = ""
                try:
                    err = ffmpeg_proc.stderr.read().strip()
                except Exception:
                    pass
                ffmpeg_proc = None
                active_signature = None
                msg = f"FFmpeg остановился, код {code}"
                if err:
                    msg += ": " + err[-600:]
                write_state("error", msg)
                logging.error(msg)
                retry_after = time.time() + 4

            if ffmpeg_proc is None and time.time() >= retry_after:
                try:
                    start_ffmpeg(cfg)
                    active_signature = sig
                    src = cfg["usb_device"] if cfg["source_type"] == "usb" else cfg["network_url"]
                    write_state("recording", "Тестовая запись идёт",
                                source_type=cfg["source_type"], source=src)
                    retry_after = 0
                except Exception as exc:
                    msg = str(exc)
                    write_state("error", msg)
                    logging.error(msg)
                    retry_after = time.time() + 5

            time.sleep(1)
    finally:
        stop_ffmpeg()
        write_state("offline", "Тестовый демон остановлен")


if __name__ == "__main__":
    main()
