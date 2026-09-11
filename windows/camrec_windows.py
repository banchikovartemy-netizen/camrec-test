#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import quote, urlsplit, urlunsplit

from camrec_windows_actions import WindowsActionsMixin

APP_NAME = 'CamRec'
LOCALAPPDATA = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
DATA_DIR = LOCALAPPDATA / APP_NAME
ARCHIVE_DIR = DATA_DIR / 'archive'
CONFIG_FILE = DATA_DIR / 'settings.json'
CREDS_FILE = DATA_DIR / 'credentials.json'
PREVIEW_URL_OUT = 'udp://127.0.0.1:23002?pkt_size=1316'

RES_MAP = {'640p': '640x480', '720p': '1280x720', '960p': '1280x960', '1080p': '1920x1080'}
REV_RES = {v: k for k, v in RES_MAP.items()}
DEFAULT_CONFIG = {
    'status': 'stop', 'source_type': 'usb', 'video_device': '', 'audio_device': '',
    'network_url': '', 'resolution': '640x480', 'framerate': 30,
    'retention_days': 7, 'segment_minutes': 5, 'audio_enabled': False,
    'autostart_enabled': False, 'autostart_source': 'usb',
    'launch_with_windows': False, 'saved_dir': '',
}


def no_window_flags(new_group=False):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if new_group:
        flags |= getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    return flags


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path, default):
    try:
        with path.open('r', encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else default.copy()
    except Exception:
        return default.copy()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def authenticated_url(raw_url):
    raw_url = raw_url.strip()
    creds = read_json(CREDS_FILE, {'username': '', 'password': ''})
    username = str(creds.get('username', ''))
    password = str(creds.get('password', ''))
    if not username:
        return raw_url
    parts = urlsplit(raw_url)
    host = parts.hostname or ''
    port = f':{parts.port}' if parts.port else ''
    auth = quote(username, safe='')
    if password:
        auth += ':' + quote(password, safe='')
    return urlunsplit((parts.scheme, f'{auth}@{host}{port}', parts.path, parts.query, parts.fragment))


def list_dshow_devices():
    video, audio = [], []
    try:
        p = subprocess.run(
            ['ffmpeg', '-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
            capture_output=True, text=True, timeout=8, creationflags=no_window_flags()
        )
        section = None
        for line in p.stderr.splitlines():
            low = line.lower()
            if 'directshow video devices' in low:
                section = 'video'
                continue
            if 'directshow audio devices' in low:
                section = 'audio'
                continue
            if 'alternative name' in low:
                continue
            m = re.search(r'"([^"\r\n]+)"', line)
            if not m or section not in ('video', 'audio'):
                continue
            name = m.group(1)
            target = video if section == 'video' else audio
            if name not in target:
                target.append(name)
    except Exception:
        pass
    return video, audio


class CamRecWindows(WindowsActionsMixin, tk.Tk):
    default_config = DEFAULT_CONFIG

    def __init__(self):
        ensure_dirs()
        super().__init__()
        self.title('CamRec — Windows')
        self.geometry('1080x790')
        self.minsize(940, 670)
        self.cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG)
        for k, v in DEFAULT_CONFIG.items():
            self.cfg.setdefault(k, v)
        self.rec_proc = None
        self.rec_error = ''
        self.preview_window = None
        self.status_text = 'Остановлено'
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._build()
        self._load_to_widgets()
        self.refresh_devices()
        self.refresh_archive()
        self.after(400, self.apply_autostart)
        self.after(1000, self.tick)

    @staticmethod
    def no_window_flags():
        return no_window_flags()

    @staticmethod
    def tk_bool(value):
        return tk.BooleanVar(value=value)

    @staticmethod
    def tk_str(value):
        return tk.StringVar(value=value)

    def _modal(self, title):
        w = tk.Toplevel(self)
        w.title(title)
        w.transient(self)
        w.grab_set()
        return w

    def default_saved_dir(self):
        return Path.home() / 'Videos' / 'CamRec' / 'Saved'

    def _build(self):
        top = ttk.Frame(self, padding=12)
        top.pack(fill='x')
        ttk.Label(top, text='CamRec', font=('', 20, 'bold')).pack(side='left')
        self.status_label = ttk.Label(top, text='Статус: Остановлено')
        self.status_label.pack(side='right')

        settings = ttk.LabelFrame(self, text='Источник и запись', padding=10)
        settings.pack(fill='x', padx=12, pady=(0, 8))
        self.source_var = tk.StringVar(value='usb')
        ttk.Radiobutton(settings, text='Веб-камера', variable=self.source_var, value='usb', command=self.source_changed).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(settings, text='IP / RTSP камера', variable=self.source_var, value='network', command=self.source_changed).grid(row=0, column=1, sticky='w', padx=(14, 0))
        ttk.Button(settings, text='Подключить IP-камеру…', command=self.open_ip_dialog).grid(row=0, column=2, sticky='w', padx=6)

        ttk.Label(settings, text='Веб-камера:').grid(row=1, column=0, sticky='e', pady=5)
        self.video_var = tk.StringVar()
        self.video_combo = ttk.Combobox(settings, textvariable=self.video_var, width=48, state='readonly')
        self.video_combo.grid(row=1, column=1, columnspan=3, sticky='ew', padx=6)
        ttk.Button(settings, text='Обновить устройства', command=self.refresh_devices).grid(row=1, column=4, padx=6)
        self.network_label = ttk.Label(settings, text='IP URL: не задан')
        self.network_label.grid(row=2, column=0, columnspan=5, sticky='w', pady=(0, 4))

        ttk.Label(settings, text='Разрешение:').grid(row=3, column=0, sticky='e', pady=5)
        self.res_var = tk.StringVar(value='640p')
        ttk.Combobox(settings, textvariable=self.res_var, values=list(RES_MAP), width=12, state='readonly').grid(row=3, column=1, sticky='w', padx=6)
        ttk.Label(settings, text='FPS:').grid(row=3, column=2, sticky='e')
        self.fps_var = tk.IntVar(value=30)
        ttk.Spinbox(settings, from_=1, to=60, textvariable=self.fps_var, width=8).grid(row=3, column=3, sticky='w', padx=6)

        self.audio_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text='Записывать звук', variable=self.audio_var, command=self.source_changed).grid(row=4, column=0, sticky='w', pady=5)
        self.audio_label = ttk.Label(settings, text='Микрофон:')
        self.audio_label.grid(row=4, column=1, sticky='e')
        self.audio_device_var = tk.StringVar()
        self.audio_combo = ttk.Combobox(settings, textvariable=self.audio_device_var, width=44, state='readonly')
        self.audio_combo.grid(row=4, column=2, columnspan=3, sticky='ew', padx=6)

        ttk.Label(settings, text='Хранить, дней:').grid(row=5, column=0, sticky='e', pady=5)
        self.retention_var = tk.IntVar(value=7)
        ttk.Spinbox(settings, from_=1, to=3650, textvariable=self.retention_var, width=8).grid(row=5, column=1, sticky='w', padx=6)
        ttk.Label(settings, text='Один файл, минут:').grid(row=5, column=2, sticky='e')
        self.segment_var = tk.IntVar(value=5)
        ttk.Spinbox(settings, from_=1, to=240, textvariable=self.segment_var, width=8).grid(row=5, column=3, sticky='w', padx=6)

        ttk.Label(settings, text='Папка «Сохранить»:').grid(row=6, column=0, sticky='e', pady=5)
        self.saved_dir_var = tk.StringVar()
        ttk.Entry(settings, textvariable=self.saved_dir_var, width=48).grid(row=6, column=1, columnspan=3, sticky='ew', padx=6)
        ttk.Button(settings, text='Выбрать…', command=self.choose_saved_dir).grid(row=6, column=4, padx=6)
        settings.columnconfigure(3, weight=1)

        buttons = ttk.Frame(self, padding=(12, 2))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Сохранить настройки', command=self.save_settings).pack(side='left', padx=(0, 5))
        ttk.Button(buttons, text='Live', command=self.live).pack(side='left', padx=5)
        ttk.Button(buttons, text='▶ Начать запись', command=self.start_recording).pack(side='left', padx=5)
        ttk.Button(buttons, text='■ Остановить', command=self.stop_recording).pack(side='left', padx=5)
        ttk.Button(buttons, text='💾 Сохранить', command=self.save_recording).pack(side='left', padx=5)
        ttk.Button(buttons, text='Экран записи', command=self.open_recording_preview).pack(side='left', padx=5)
        ttk.Button(buttons, text='Автозапуск…', command=self.open_autostart_dialog).pack(side='left', padx=5)

        archive = ttk.LabelFrame(self, text='Записи', padding=8)
        archive.pack(fill='both', expand=True, padx=12, pady=10)
        cols = ('time', 'size', 'source')
        self.tree = ttk.Treeview(archive, columns=cols, show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Файл')
        self.tree.heading('time', text='Изменён')
        self.tree.heading('size', text='Размер')
        self.tree.heading('source', text='Источник')
        self.tree.column('#0', width=430)
        self.tree.column('time', width=160)
        self.tree.column('size', width=100)
        self.tree.column('source', width=120)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(archive, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Double-1>', lambda e: self.play_selected())

        bottom = ttk.Frame(self, padding=(12, 0, 12, 12))
        bottom.pack(fill='x')
        ttk.Button(bottom, text='Открыть выбранное', command=self.play_selected).pack(side='left')
        ttk.Button(bottom, text='Экспорт…', command=self.export_selected).pack(side='left', padx=6)
        ttk.Button(bottom, text='Обновить список', command=self.refresh_archive).pack(side='left', padx=6)
        ttk.Button(bottom, text='Открыть архив', command=lambda: os.startfile(ARCHIVE_DIR)).pack(side='left', padx=6)
        ttk.Button(bottom, text='Открыть сохранённые', command=self.open_saved_folder).pack(side='left', padx=6)
        ttk.Button(bottom, text='Проверить источник', command=self.probe_source).pack(side='right')

    def _load_to_widgets(self):
        self.source_var.set(self.cfg.get('source_type', 'usb'))
        self.video_var.set(self.cfg.get('video_device', ''))
        self.audio_device_var.set(self.cfg.get('audio_device', ''))
        self.res_var.set(REV_RES.get(self.cfg.get('resolution'), '640p'))
        self.fps_var.set(self.cfg.get('framerate', 30))
        self.retention_var.set(self.cfg.get('retention_days', 7))
        self.segment_var.set(self.cfg.get('segment_minutes', 5))
        self.audio_var.set(bool(self.cfg.get('audio_enabled', False)))
        self.saved_dir_var.set(self.cfg.get('saved_dir') or str(self.default_saved_dir()))
        self.update_network_label()
        self.source_changed()

    def collect_settings(self):
        self.cfg.update({
            'source_type': self.source_var.get(),
            'video_device': self.video_var.get().strip(),
            'audio_device': self.audio_device_var.get().strip(),
            'resolution': RES_MAP.get(self.res_var.get(), '640x480'),
            'framerate': max(1, min(60, int(self.fps_var.get()))),
            'retention_days': max(1, int(self.retention_var.get())),
            'segment_minutes': max(1, int(self.segment_var.get())),
            'audio_enabled': bool(self.audio_var.get()),
            'saved_dir': self.saved_dir_var.get().strip() or str(self.default_saved_dir()),
        })
        return self.cfg

    def save_settings(self, quiet=False):
        try:
            write_json(CONFIG_FILE, self.collect_settings())
            if not quiet:
                messagebox.showinfo('CamRec', 'Настройки сохранены.')
            return True
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))
            return False

    def refresh_devices(self):
        video, audio = list_dshow_devices()
        self.video_combo['values'] = video
        self.audio_combo['values'] = audio
        if not self.video_var.get() and video:
            self.video_var.set(video[0])
        if not self.audio_device_var.get() and audio:
            self.audio_device_var.set(audio[0])

    def source_changed(self):
        is_usb = self.source_var.get() == 'usb'
        self.video_combo.configure(state='readonly' if is_usb else 'disabled')
        self.audio_label.config(text='Микрофон:' if is_usb else 'Звук камеры:')
        self.audio_combo.configure(state='readonly' if is_usb and self.audio_var.get() else 'disabled')
        self.update_network_label()

    def update_network_label(self):
        url = self.cfg.get('network_url', '')
        self.network_label.config(text=f'IP URL: {url if url else "не задан"}')

    def build_record_command(self):
        cfg = self.collect_settings()
        prefix = 'webcam' if cfg['source_type'] == 'usb' else 'camera'
        output = str(ARCHIVE_DIR / f'{prefix}_%Y-%m-%d_%H-%M-%S.mp4')
        seg = cfg['segment_minutes'] * 60
        fps = cfg['framerate']
        gop = max(2, fps * 2)
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y']
        if cfg['source_type'] == 'usb':
            if not cfg['video_device']:
                raise RuntimeError('Выбери веб-камеру.')
            cmd += ['-thread_queue_size', '512', '-f', 'dshow', '-framerate', str(fps), '-video_size', cfg['resolution'], '-i', f"video={cfg['video_device']}"]
            video_map = '0:v:0'
            audio_map = None
            if cfg['audio_enabled']:
                if not cfg['audio_device']:
                    raise RuntimeError('Выбери микрофон или отключи запись звука.')
                cmd += ['-thread_queue_size', '512', '-f', 'dshow', '-i', f"audio={cfg['audio_device']}"]
                audio_map = '1:a:0'
        else:
            url = authenticated_url(cfg.get('network_url', ''))
            if not url:
                raise RuntimeError('Сначала укажи URL IP-камеры.')
            if url.lower().startswith('rtsp://'):
                cmd += ['-rtsp_transport', 'tcp']
            cmd += ['-i', url]
            video_map = '0:v:0'
            audio_map = '0:a?'
        cmd += ['-map', video_map]
        if cfg['audio_enabled'] and audio_map:
            cmd += ['-map', audio_map]
        cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30', '-pix_fmt', 'yuv420p', '-g', str(gop), '-keyint_min', str(gop), '-sc_threshold', '0']
        if cfg['audio_enabled']:
            cmd += ['-c:a', 'aac', '-b:a', '96k', '-ar', '48000', '-af', 'aresample=async=1:first_pts=0']
        else:
            cmd += ['-an']
        cmd += ['-f', 'segment', '-segment_time', str(seg), '-segment_format', 'mp4', '-segment_format_options', 'movflags=+faststart', '-reset_timestamps', '1', '-strftime', '1', output]
        cmd += ['-map', video_map, '-an', '-vf', 'scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1', '-r', '10', '-c:v', 'mpeg2video', '-q:v', '8', '-g', '10', '-bf', '0', '-f', 'mpegts', PREVIEW_URL_OUT]
        return cmd

    def start_recording(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.open_recording_preview()
            return
        try:
            self.save_settings(quiet=True)
            self.rec_error = ''
            self.rec_proc = subprocess.Popen(self.build_record_command(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, creationflags=no_window_flags(new_group=True))
            self.cfg['status'] = 'play'
            write_json(CONFIG_FILE, self.cfg)
            self.status_text = 'Запуск…'
            threading.Thread(target=self.watch_recorder, daemon=True).start()
            self.after(700, self.open_recording_preview)
        except Exception as exc:
            self.rec_proc = None
            self.status_text = 'Ошибка'
            messagebox.showerror('CamRec', str(exc))

    def watch_recorder(self):
        proc = self.rec_proc
        if not proc:
            return
        time.sleep(0.7)
        if proc.poll() is None:
            self.status_text = '● Идёт запись'
        err = ''
        try:
            if proc.stderr:
                err = proc.stderr.read()
        except Exception:
            pass
        if self.rec_proc is proc and proc.poll() is not None:
            self.rec_error = err[-1800:]
            if self.cfg.get('status') == 'play':
                self.status_text = 'Ошибка' if proc.returncode else 'Остановлено'

    def stop_recording(self):
        proc = self.rec_proc
        self.rec_proc = None
        self.cfg['status'] = 'stop'
        try:
            write_json(CONFIG_FILE, self.cfg)
        except Exception:
            pass
        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write('q\n')
                    proc.stdin.flush()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
        self.status_text = 'Остановлено'
        self.close_preview_window()
        self.refresh_archive()

    def live(self):
        self.save_settings(quiet=True)
        try:
            if self.source_var.get() == 'usb':
                if not self.video_var.get():
                    raise RuntimeError('Выбери веб-камеру.')
                cmd = ['ffplay', '-f', 'dshow', '-video_size', RES_MAP.get(self.res_var.get(), '640x480'), '-framerate', str(self.fps_var.get()), '-i', f'video={self.video_var.get()}']
            else:
                cmd = ['ffplay', authenticated_url(self.cfg.get('network_url', ''))]
            subprocess.Popen(cmd, creationflags=no_window_flags())
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))

    def probe_source(self):
        self.save_settings(quiet=True)
        if self.source_var.get() == 'network':
            creds = read_json(CREDS_FILE, {'username': '', 'password': ''})
            self.probe_network(self.cfg.get('network_url', ''), creds.get('username', ''), creds.get('password', ''))
            return
        if not self.video_var.get():
            messagebox.showwarning('CamRec', 'Выбери веб-камеру.')
            return
        try:
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'dshow', '-t', '1', '-i', f'video={self.video_var.get()}', '-f', 'null', '-']
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=6, creationflags=no_window_flags())
            if p.returncode == 0:
                messagebox.showinfo('CamRec', 'Веб-камера отвечает.')
            else:
                messagebox.showwarning('CamRec', 'FFmpeg видит устройство, но тест завершился с ошибкой:\n' + p.stderr[-800:])
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))

    def tick(self):
        if self.rec_proc and self.rec_proc.poll() is not None and self.cfg.get('status') == 'play':
            self.status_text = 'Ошибка'
        self.status_label.config(text='Статус: ' + self.status_text)
        self.refresh_archive()
        self.cleanup_old()
        self.after(2000, self.tick)

    def on_close(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.stop_recording()
        self.close_preview_window()
        self.destroy()


def main():
    ensure_dirs()
    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('CamRec', 'FFmpeg не найден. Запусти установщик CamRec ещё раз или установи FFmpeg.')
        return
    CamRecWindows().mainloop()


if __name__ == '__main__':
    main()
