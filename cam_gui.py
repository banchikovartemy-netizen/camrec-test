#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
from urllib.parse import quote, urlsplit, urlunsplit

DATA_DIR = Path("/var/lib/camrec-test")
VIDEO_DIR = DATA_DIR / "archive"
CONFIG_FILE = DATA_DIR / "settings.json"
CREDS_FILE = DATA_DIR / "credentials.json"
STATE_FILE = Path("/run/camrec-test/state.json")

DEFAULT = {
    "status": "stop",
    "source_type": "usb",
    "usb_device": "/dev/video0",
    "network_url": "",
    "resolution": "640x480",
    "framerate": 30,
    "retention_days": 2,
    "segment_minutes": 2
}

RES_MAP = {
    "640p": "640x480",
    "720p": "1280x720",
    "960p": "1280x960",
    "1080p": "1920x1080"
}
REV_RES = {v: k for k, v in RES_MAP.items()}
files_cache = []


def read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else default.copy()
    except Exception:
        return default.copy()


def atomic_write(path, payload, mode=0o664):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_config():
    cfg = DEFAULT.copy()
    cfg.update(read_json(CONFIG_FILE, {}))
    return cfg


def read_credentials():
    data = read_json(CREDS_FILE, {"username": "", "password": ""})
    return str(data.get("username", "")), str(data.get("password", ""))


def write_config(cfg):
    try:
        atomic_write(CONFIG_FILE, cfg, 0o664)
        return True
    except PermissionError:
        messagebox.showerror(
            "Нет доступа",
            "Нет прав на изменение настроек.\n"
            "После установки выйдите из сеанса Linux и войдите снова."
        )
        return False
    except Exception as exc:
        messagebox.showerror("Ошибка", str(exc))
        return False


def write_credentials(username, password):
    try:
        atomic_write(
            CREDS_FILE,
            {"username": username, "password": password},
            0o660
        )
        return True
    except PermissionError:
        messagebox.showerror(
            "Нет доступа",
            "Не удалось сохранить логин/пароль.\n"
            "После установки выйдите из сеанса Linux и войдите снова."
        )
        return False
    except Exception as exc:
        messagebox.showerror("Ошибка", str(exc))
        return False


def authenticated_url(raw_url):
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


def collect_settings():
    cfg = read_config()
    cfg["source_type"] = "usb" if source_var.get() == "USB-веб-камера" else "network"
    cfg["usb_device"] = usb_var.get().strip() or "/dev/video0"
    cfg["resolution"] = RES_MAP.get(res_var.get(), "1280x720")
    cfg["framerate"] = int(fps_var.get())
    cfg["retention_days"] = int(retention_var.get())
    cfg["segment_minutes"] = int(segment_var.get())
    return cfg


def save_settings(show=True):
    cfg = collect_settings()
    if write_config(cfg) and show:
        messagebox.showinfo("CamRec Test", "Настройки сохранены.")


def connection_dialog():
    cfg = read_config()
    old_user, old_pass = read_credentials()

    win = tk.Toplevel(root)
    win.title("Подключение к IP-камере")
    win.geometry("520x330")
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()
    win.configure(bg="#17191f")

    tk.Label(
        win, text="Подключение к сетевой камере",
        font=("Arial", 14, "bold"), bg="#17191f", fg="#ffffff"
    ).pack(pady=(18, 6))

    tk.Label(
        win,
        text="Введите адрес потока и данные авторизации.",
        bg="#17191f", fg="#aab2bf"
    ).pack(pady=(0, 14))

    form = tk.Frame(win, bg="#17191f")
    form.pack(fill=tk.X, padx=26)

    url_v = tk.StringVar(value=cfg.get("network_url", ""))
    user_v = tk.StringVar(value=old_user)
    pass_v = tk.StringVar(value=old_pass)

    labels = ["RTSP / HTTP URL:", "Логин:", "Пароль:"]
    vars_ = [url_v, user_v, pass_v]

    for i, text in enumerate(labels):
        tk.Label(
            form, text=text, bg="#17191f", fg="#dddddd",
            anchor="e", width=15
        ).grid(row=i, column=0, padx=(0, 8), pady=7, sticky="e")

        ent = tk.Entry(
            form, textvariable=vars_[i],
            width=40, bg="#242730", fg="#ffffff",
            insertbackground="#ffffff", relief="flat"
        )
        if i == 2:
            ent.config(show="●")
        ent.grid(row=i, column=1, pady=7, sticky="we")

    show_pass = tk.BooleanVar(value=False)

    def toggle_show():
        for child in form.grid_slaves(row=2, column=1):
            if isinstance(child, tk.Entry):
                child.config(show="" if show_pass.get() else "●")

    tk.Checkbutton(
        win, text="Показать пароль",
        variable=show_pass, command=toggle_show,
        bg="#17191f", fg="#cccccc",
        selectcolor="#242730", activebackground="#17191f",
        activeforeground="#ffffff"
    ).pack(anchor="w", padx=184, pady=(2, 8))

    status = tk.Label(
        win, text="", bg="#17191f", fg="#aab2bf",
        wraplength=470, justify=tk.LEFT
    )
    status.pack(fill=tk.X, padx=26, pady=(2, 10))

    buttons = tk.Frame(win, bg="#17191f")
    buttons.pack(pady=4)

    def save_only():
        url = url_v.get().strip()
        if not url.lower().startswith(("rtsp://", "http://", "https://")):
            messagebox.showerror(
                "Адрес камеры",
                "Введите полный URL, например:\n"
                "rtsp://192.168.1.50:554/stream1",
                parent=win
            )
            return
        cfg2 = read_config()
        cfg2["source_type"] = "network"
        cfg2["network_url"] = url
        if not write_credentials(user_v.get().strip(), pass_v.get()):
            return
        if not write_config(cfg2):
            return
        source_var.set("IP / RTSP камера")
        update_source_fields()
        win.destroy()

    def test_connection():
        url = url_v.get().strip()
        if not url.lower().startswith(("rtsp://", "http://", "https://")):
            messagebox.showerror("Адрес камеры", "Неверный RTSP/HTTP URL.", parent=win)
            return

        if not write_credentials(user_v.get().strip(), pass_v.get()):
            return

        cfg2 = read_config()
        cfg2["network_url"] = url
        cfg2["source_type"] = "network"
        write_config(cfg2)

        status.config(text="Проверяю подключение...", fg="#5da9ff")

        def worker():
            full_url = authenticated_url(url)
            cmd = ["ffprobe", "-v", "error"]
            if full_url.lower().startswith("rtsp://"):
                cmd += ["-rtsp_transport", "tcp"]
            cmd += [
                "-i", full_url,
                "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                "-of", "default=noprint_wrappers=1"
            ]

            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if r.returncode == 0:
                    msg = r.stdout.strip() or "Камера ответила."
                    root.after(0, lambda: status.config(
                        text="Подключение успешно:\n" + msg,
                        fg="#55d17a"
                    ))
                else:
                    err = (r.stderr or "Не удалось подключиться.")[-800:]
                    root.after(0, lambda: status.config(
                        text="Ошибка подключения:\n" + err,
                        fg="#ff6b6b"
                    ))
            except subprocess.TimeoutExpired:
                root.after(0, lambda: status.config(
                    text="Камера не ответила за 8 секунд.",
                    fg="#ff6b6b"
                ))
            except FileNotFoundError:
                root.after(0, lambda: status.config(
                    text="Не найден ffprobe.",
                    fg="#ff6b6b"
                ))

        threading.Thread(target=worker, daemon=True).start()

    btn_style = dict(
        bg="#2b303b", fg="#ffffff",
        activebackground="#3a414f", activeforeground="#ffffff",
        relief="flat", padx=12, pady=7,
        font=("Arial", 10, "bold")
    )

    tk.Button(buttons, text="Проверить", command=test_connection, **btn_style).pack(side=tk.LEFT, padx=5)
    tk.Button(buttons, text="Сохранить", command=save_only, **btn_style).pack(side=tk.LEFT, padx=5)
    tk.Button(buttons, text="Отмена", command=win.destroy, **btn_style).pack(side=tk.LEFT, padx=5)


def toggle_record():
    cfg = collect_settings()

    if cfg["source_type"] == "network" and not cfg.get("network_url"):
        connection_dialog()
        return

    current = read_config()
    cfg["network_url"] = current.get("network_url", "")
    cfg["status"] = "stop" if current.get("status") == "play" else "play"

    if write_config(cfg):
        update_record_button()


def update_record_button():
    cfg = read_config()
    record_btn.config(
        text="Остановить тестовую запись" if cfg.get("status") == "play"
        else "Запустить тестовую запись"
    )


def update_source_fields(*args):
    usb = source_var.get() == "USB-веб-камера"
    usb_entry.config(state="normal" if usb else "disabled")
    connect_btn.config(state="disabled" if usb else "normal")
    note.config(
        text=(
            "Домашний тест: обычно /dev/video0."
            if usb else
            "IP-камера: нажмите «Подключить IP-камеру» и введите URL, логин и пароль."
        )
    )


def live_preview():
    cfg = collect_settings()

    if not shutil.which("ffplay"):
        messagebox.showerror("Live", "Не найден ffplay.")
        return

    if cfg["source_type"] == "usb":
        was_playing = read_config().get("status") == "play"
        if was_playing:
            if not messagebox.askokcancel(
                "Live USB",
                "Для USB-камеры тестовая запись временно остановится.\n"
                "После закрытия live-окна запись возобновится."
            ):
                return
            temp = read_config()
            temp["status"] = "stop"
            write_config(temp)
            time.sleep(1.2)

        def worker_usb():
            try:
                subprocess.run([
                    "ffplay",
                    "-f", "v4l2",
                    "-channel", "0",
                    "-pixel_format", "yuyv422",
                    "-framerate", str(cfg["framerate"]),
                    "-video_size", cfg["resolution"],
                    "-i", cfg["usb_device"],
                    "-window_title", "CamRec Test — USB Live"
                ])
            finally:
                if was_playing:
                    temp = read_config()
                    temp["status"] = "play"
                    write_config(temp)
                    root.after(0, update_record_button)

        threading.Thread(target=worker_usb, daemon=True).start()
    else:
        current = read_config()
        raw = current.get("network_url", "")
        if not raw:
            connection_dialog()
            return
        url = authenticated_url(raw)
        cmd = ["ffplay"]
        if url.lower().startswith("rtsp://"):
            cmd += ["-rtsp_transport", "tcp"]
        cmd += [url, "-window_title", "CamRec Test — IP Live"]
        subprocess.Popen(cmd)


def refresh_archive():
    global files_cache
    try:
        files_cache = sorted(
            VIDEO_DIR.glob("*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
    except Exception:
        files_cache = []

    listbox.delete(0, tk.END)
    for p in files_cache:
        try:
            st = p.stat()
            stamp = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(st.st_mtime))
            mb = st.st_size / 1024 / 1024
            listbox.insert(tk.END, f"{stamp}   {mb:.1f} MB   {p.name}")
        except Exception:
            listbox.insert(tk.END, p.name)

    root.after(4000, refresh_archive)


def play_selected():
    sel = listbox.curselection()
    if not sel:
        return
    p = files_cache[sel[0]]
    if shutil.which("ffplay"):
        subprocess.Popen(["ffplay", "-autoexit", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


def refresh_status():
    state = read_json(STATE_FILE, {"state": "offline", "message": "Демон не отвечает"})
    mapping = {
        "recording": ("● Запись идёт", "#55d17a"),
        "stopped": ("■ Запись остановлена", "#e5b454"),
        "error": ("! Ошибка", "#ff6b6b"),
        "starting": ("… Запуск", "#5da9ff"),
        "offline": ("× Демон не запущен", "#9aa4b2")
    }
    txt, col = mapping.get(state.get("state"), ("?", "#9aa4b2"))
    status_label.config(text=txt, fg=col)
    status_msg.config(text=state.get("message", ""))
    root.after(1000, refresh_status)


root = tk.Tk()
root.title("CamRec Test — USB / IP камера")
root.geometry("860x620")
root.minsize(800, 560)
root.configure(bg="#17191f")

style = ttk.Style()
style.theme_use("clam")

cfg = read_config()

status_frame = tk.Frame(root, bg="#17191f")
status_frame.pack(fill=tk.X, padx=16, pady=(14, 6))

status_label = tk.Label(
    status_frame, text="…", bg="#17191f", fg="#5da9ff",
    font=("Arial", 13, "bold"), anchor="w"
)
status_label.pack(fill=tk.X)

status_msg = tk.Label(
    status_frame, text="", bg="#17191f", fg="#aab2bf",
    font=("Arial", 9), anchor="w", justify=tk.LEFT, wraplength=800
)
status_msg.pack(fill=tk.X, pady=(3, 0))

settings = tk.LabelFrame(
    root, text=" Источник для теста ",
    bg="#17191f", fg="#ffffff",
    font=("Arial", 10, "bold")
)
settings.pack(fill=tk.X, padx=16, pady=8)

source_var = tk.StringVar(
    value="USB-веб-камера" if cfg.get("source_type") == "usb" else "IP / RTSP камера"
)
usb_var = tk.StringVar(value=cfg.get("usb_device", "/dev/video0"))
res_var = tk.StringVar(value=REV_RES.get(cfg.get("resolution"), "640p"))
fps_var = tk.StringVar(value=str(cfg.get("framerate", 30)))
retention_var = tk.StringVar(value=str(cfg.get("retention_days", 2)))
segment_var = tk.StringVar(value=str(cfg.get("segment_minutes", 2)))

tk.Label(settings, text="Тип:", bg="#17191f", fg="#ddd").grid(row=0, column=0, padx=6, pady=8, sticky="e")
source_combo = ttk.Combobox(
    settings, textvariable=source_var,
    values=["USB-веб-камера", "IP / RTSP камера"],
    state="readonly", width=18
)
source_combo.grid(row=0, column=1, padx=6, pady=8, sticky="w")
source_combo.bind("<<ComboboxSelected>>", update_source_fields)

tk.Label(settings, text="USB:", bg="#17191f", fg="#ddd").grid(row=0, column=2, padx=6, pady=8, sticky="e")
usb_entry = tk.Entry(settings, textvariable=usb_var, width=18)
usb_entry.grid(row=0, column=3, padx=6, pady=8, sticky="w")

tk.Label(settings, text="Качество:", bg="#17191f", fg="#ddd").grid(row=0, column=4, padx=6, pady=8, sticky="e")
ttk.Combobox(
    settings, textvariable=res_var,
    values=list(RES_MAP.keys()), state="readonly", width=8
).grid(row=0, column=5, padx=6, pady=8)

tk.Label(settings, text="FPS:", bg="#17191f", fg="#ddd").grid(row=0, column=6, padx=6, pady=8, sticky="e")
ttk.Combobox(
    settings, textvariable=fps_var,
    values=["10","15","20","25","30","60"],
    state="readonly", width=5
).grid(row=0, column=7, padx=6, pady=8)

tk.Label(settings, text="Хранить дней:", bg="#17191f", fg="#ddd").grid(row=1, column=0, padx=6, pady=8, sticky="e")
ttk.Spinbox(settings, from_=1, to=365, textvariable=retention_var, width=6).grid(row=1, column=1, padx=6, pady=8, sticky="w")

tk.Label(settings, text="Файл минут:", bg="#17191f", fg="#ddd").grid(row=1, column=2, padx=6, pady=8, sticky="e")
ttk.Spinbox(settings, from_=1, to=120, textvariable=segment_var, width=6).grid(row=1, column=3, padx=6, pady=8, sticky="w")

connect_btn = tk.Button(
    settings, text="Подключить IP-камеру",
    command=connection_dialog,
    bg="#2b303b", fg="#fff",
    activebackground="#3a414f", activeforeground="#fff",
    relief="flat", padx=10, pady=7,
    font=("Arial", 10, "bold")
)
connect_btn.grid(row=1, column=4, columnspan=4, padx=6, pady=8, sticky="w")

note = tk.Label(settings, text="", bg="#17191f", fg="#aab2bf", anchor="w")
note.grid(row=2, column=0, columnspan=8, padx=8, pady=(0, 8), sticky="w")

btns = tk.Frame(root, bg="#17191f")
btns.pack(fill=tk.X, padx=16, pady=6)

button_style = dict(
    bg="#2b303b", fg="#fff",
    activebackground="#3a414f", activeforeground="#fff",
    relief="flat", padx=10, pady=8,
    font=("Arial", 10, "bold")
)

record_btn = tk.Button(btns, command=toggle_record, **button_style)
record_btn.pack(side=tk.LEFT, padx=(0, 5))
tk.Button(btns, text="Live", command=live_preview, **button_style).pack(side=tk.LEFT, padx=5)
tk.Button(btns, text="Сохранить настройки", command=save_settings, **button_style).pack(side=tk.LEFT, padx=5)

tk.Label(
    root, text="Тестовый архив",
    bg="#17191f", fg="#ddd",
    font=("Arial", 10, "bold")
).pack(anchor="w", padx=16, pady=(8, 2))

listbox = tk.Listbox(
    root, bg="#0d1117", fg="#e6edf3",
    selectbackground="#2f81f7", selectforeground="#fff",
    relief="flat", font=("Arial", 10)
)
listbox.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))
listbox.bind("<Double-1>", lambda e: play_selected())

tk.Button(
    root, text="Просмотреть выбранный файл",
    command=play_selected, **button_style
).pack(pady=(0, 14))

update_source_fields()
update_record_button()
refresh_archive()
refresh_status()
root.mainloop()
