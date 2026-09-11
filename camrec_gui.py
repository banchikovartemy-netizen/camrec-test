#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote, urlsplit, urlunsplit

DATA_DIR = Path('/var/lib/camrec')
ARCHIVE_DIR = DATA_DIR / 'archive'
CONFIG_FILE = DATA_DIR / 'settings.json'
CREDS_FILE = DATA_DIR / 'credentials.json'
STATE_FILE = Path('/run/camrec/state.json')

RES_MAP = {'640p': '640x480', '720p': '1280x720', '960p': '1280x960', '1080p': '1920x1080'}
REV_RES = {v: k for k, v in RES_MAP.items()}
DEFAULT_CONFIG = {
    'status': 'stop', 'source_type': 'usb', 'usb_device': '/dev/video0', 'network_url': '',
    'resolution': '640x480', 'framerate': 30, 'retention_days': 7, 'segment_minutes': 5,
    'audio_enabled': False, 'usb_audio_device': 'default', 'autostart_enabled': False,
    'autostart_source': 'usb',
}


def read_json(path, default):
    try:
        with path.open('r', encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else default.copy()
    except Exception:
        return default.copy()


def write_json_inplace(path, data, mode=0o664):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(path, mode)
    except OSError:
        pass


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


class CamRecApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('CamRec')
        self.geometry('980x690')
        self.minsize(880, 600)
        self.cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG)
        for k, v in DEFAULT_CONFIG.items():
            self.cfg.setdefault(k, v)
        self._build()
        self._load_to_widgets()
        self.refresh_audio_devices()
        self.refresh_archive()
        self.after(250, self.apply_autostart_on_gui_launch)
        self.after(1000, self.tick)

    def _build(self):
        top = ttk.Frame(self, padding=12)
        top.pack(fill='x')
        ttk.Label(top, text='CamRec', font=('', 20, 'bold')).pack(side='left')
        self.status_label = ttk.Label(top, text='Статус: …')
        self.status_label.pack(side='right')

        settings = ttk.LabelFrame(self, text='Источник и запись', padding=10)
        settings.pack(fill='x', padx=12, pady=(0, 8))

        self.source_var = tk.StringVar(value='usb')
        ttk.Radiobutton(settings, text='USB-веб-камера', variable=self.source_var, value='usb', command=self.source_changed).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(settings, text='IP / RTSP камера', variable=self.source_var, value='network', command=self.source_changed).grid(row=0, column=1, sticky='w', padx=(14, 0))
        ttk.Button(settings, text='Подключить IP-камеру…', command=self.open_ip_dialog).grid(row=0, column=2, padx=8, sticky='w')

        ttk.Label(settings, text='USB устройство:').grid(row=1, column=0, sticky='e', pady=5)
        self.usb_var = tk.StringVar(value='/dev/video0')
        ttk.Entry(settings, textvariable=self.usb_var, width=24).grid(row=1, column=1, sticky='w', padx=6)
        self.network_label = ttk.Label(settings, text='IP URL: не задан')
        self.network_label.grid(row=1, column=2, columnspan=2, sticky='w', padx=8)

        ttk.Label(settings, text='Разрешение:').grid(row=2, column=0, sticky='e', pady=5)
        self.res_var = tk.StringVar(value='640p')
        ttk.Combobox(settings, textvariable=self.res_var, values=list(RES_MAP), width=12, state='readonly').grid(row=2, column=1, sticky='w', padx=6)
        ttk.Label(settings, text='FPS:').grid(row=2, column=2, sticky='e')
        self.fps_var = tk.IntVar(value=30)
        ttk.Spinbox(settings, from_=1, to=60, textvariable=self.fps_var, width=8).grid(row=2, column=3, sticky='w', padx=6)

        self.audio_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text='Записывать звук', variable=self.audio_var, command=self.source_changed).grid(row=3, column=0, sticky='w', pady=5)
        self.audio_label = ttk.Label(settings, text='Микрофон:')
        self.audio_label.grid(row=3, column=1, sticky='e')
        self.audio_device_var = tk.StringVar(value='default')
        self.audio_combo = ttk.Combobox(settings, textvariable=self.audio_device_var, width=35)
        self.audio_combo.grid(row=3, column=2, columnspan=2, sticky='ew', padx=6)
        ttk.Button(settings, text='Обновить микрофоны', command=self.refresh_audio_devices).grid(row=3, column=4, padx=6)

        ttk.Label(settings, text='Хранить, дней:').grid(row=4, column=0, sticky='e', pady=5)
        self.retention_var = tk.IntVar(value=7)
        ttk.Spinbox(settings, from_=1, to=3650, textvariable=self.retention_var, width=8).grid(row=4, column=1, sticky='w', padx=6)
        ttk.Label(settings, text='Один файл, минут:').grid(row=4, column=2, sticky='e')
        self.segment_var = tk.IntVar(value=5)
        ttk.Spinbox(settings, from_=1, to=240, textvariable=self.segment_var, width=8).grid(row=4, column=3, sticky='w', padx=6)

        buttons = ttk.Frame(self, padding=(12, 2))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Сохранить настройки', command=self.save_settings).pack(side='left', padx=(0, 6))
        ttk.Button(buttons, text='Live', command=self.live).pack(side='left', padx=6)
        ttk.Button(buttons, text='▶ Начать запись', command=self.start_recording).pack(side='left', padx=6)
        ttk.Button(buttons, text='■ Остановить', command=self.stop_recording).pack(side='left', padx=6)
        ttk.Button(buttons, text='Автозапуск…', command=self.open_autostart_dialog).pack(side='left', padx=6)
        ttk.Button(buttons, text='Открыть папку записей', command=self.open_archive_folder).pack(side='left', padx=6)

        archive = ttk.LabelFrame(self, text='Записи', padding=8)
        archive.pack(fill='both', expand=True, padx=12, pady=10)
        cols = ('time', 'size', 'source')
        self.tree = ttk.Treeview(archive, columns=cols, show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Файл')
        self.tree.heading('time', text='Изменён')
        self.tree.heading('size', text='Размер')
        self.tree.heading('source', text='Источник')
        self.tree.column('#0', width=380)
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
        ttk.Button(bottom, text='Проверить источник', command=self.probe_source).pack(side='right')

    def _load_to_widgets(self):
        self.source_var.set(self.cfg.get('source_type', 'usb'))
        self.usb_var.set(self.cfg.get('usb_device', '/dev/video0'))
        self.res_var.set(REV_RES.get(self.cfg.get('resolution'), '640p'))
        self.fps_var.set(self.cfg.get('framerate', 30))
        self.retention_var.set(self.cfg.get('retention_days', 7))
        self.segment_var.set(self.cfg.get('segment_minutes', 5))
        self.audio_var.set(bool(self.cfg.get('audio_enabled', False)))
        self.audio_device_var.set(self.cfg.get('usb_audio_device', 'default'))
        self.update_network_label()
        self.source_changed()

    def collect_settings(self):
        self.cfg.update({
            'source_type': self.source_var.get(),
            'usb_device': self.usb_var.get().strip() or '/dev/video0',
            'resolution': RES_MAP.get(self.res_var.get(), '640x480'),
            'framerate': max(1, min(60, int(self.fps_var.get()))),
            'retention_days': max(1, int(self.retention_var.get())),
            'segment_minutes': max(1, int(self.segment_var.get())),
            'audio_enabled': bool(self.audio_var.get()),
            'usb_audio_device': self.audio_device_var.get().strip() or 'default',
        })
        return self.cfg

    def save_settings(self, quiet=False):
        try:
            cfg = self.collect_settings()
            write_json_inplace(CONFIG_FILE, cfg)
            if not quiet:
                messagebox.showinfo('CamRec', 'Настройки сохранены.')
            return True
        except Exception as exc:
            messagebox.showerror('CamRec', f'Не удалось сохранить настройки:\n{exc}')
            return False

    def set_status(self, value):
        if not self.save_settings(quiet=True):
            return
        cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG)
        cfg['status'] = value
        write_json_inplace(CONFIG_FILE, cfg)
        self.cfg = cfg

    def start_recording(self):
        self.collect_settings()
        if self.source_var.get() == 'network' and not self.cfg.get('network_url'):
            messagebox.showwarning('CamRec', 'Сначала подключи IP-камеру и укажи RTSP/HTTP URL.')
            return
        self.set_status('play')

    def stop_recording(self):
        self.set_status('stop')

    def source_changed(self):
        is_usb = self.source_var.get() == 'usb'
        if is_usb:
            self.audio_label.config(text='Микрофон:')
            self.audio_combo.configure(state='normal' if self.audio_var.get() else 'disabled')
        else:
            self.audio_label.config(text='Звук камеры:')
            self.audio_combo.configure(state='disabled')
        self.update_network_label()

    def update_network_label(self):
        url = self.cfg.get('network_url', '')
        self.network_label.config(text=f'IP URL: {url if url else "не задан"}')

    def open_ip_dialog(self):
        win = tk.Toplevel(self)
        win.title('Подключение к IP-камере')
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill='both', expand=True)
        creds = read_json(CREDS_FILE, {'username': '', 'password': ''})
        url_var = tk.StringVar(value=self.cfg.get('network_url', ''))
        user_var = tk.StringVar(value=creds.get('username', ''))
        pass_var = tk.StringVar(value=creds.get('password', ''))
        show_var = tk.BooleanVar(value=False)

        ttk.Label(frm, text='RTSP / HTTP URL:').grid(row=0, column=0, sticky='e', pady=5)
        ttk.Entry(frm, textvariable=url_var, width=48).grid(row=0, column=1, padx=6)
        ttk.Label(frm, text='Логин:').grid(row=1, column=0, sticky='e', pady=5)
        ttk.Entry(frm, textvariable=user_var, width=32).grid(row=1, column=1, sticky='w', padx=6)
        ttk.Label(frm, text='Пароль:').grid(row=2, column=0, sticky='e', pady=5)
        pw = ttk.Entry(frm, textvariable=pass_var, show='●', width=32)
        pw.grid(row=2, column=1, sticky='w', padx=6)
        ttk.Checkbutton(frm, text='Показать пароль', variable=show_var, command=lambda: pw.config(show='' if show_var.get() else '●')).grid(row=3, column=1, sticky='w', padx=6)
        ttk.Label(frm, text='Если включить «Записывать звук», CamRec возьмёт аудио из RTSP-потока, если камера его отдаёт.').grid(row=4, column=0, columnspan=2, sticky='w', pady=(8, 10))

        def save():
            self.cfg['network_url'] = url_var.get().strip()
            write_json_inplace(CREDS_FILE, {'username': user_var.get(), 'password': pass_var.get()}, 0o660)
            write_json_inplace(CONFIG_FILE, self.cfg)
            self.source_var.set('network')
            self.update_network_label()
            self.source_changed()
            win.destroy()

        ttk.Button(frm, text='Проверить', command=lambda: self.probe_network(url_var.get().strip(), user_var.get(), pass_var.get())).grid(row=5, column=0, pady=4)
        ttk.Button(frm, text='Сохранить', command=save).grid(row=5, column=1, sticky='w', padx=6)
        ttk.Button(frm, text='Отмена', command=win.destroy).grid(row=5, column=1, sticky='e', padx=6)

    def probe_network(self, url, username, password):
        if not url:
            messagebox.showwarning('CamRec', 'Укажи URL камеры.')
            return
        try:
            parts = urlsplit(url)
            host = parts.hostname or ''
            port = f':{parts.port}' if parts.port else ''
            if username:
                auth = quote(username, safe='') + (':' + quote(password, safe='') if password else '')
                url = urlunsplit((parts.scheme, f'{auth}@{host}{port}', parts.path, parts.query, parts.fragment))
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=index,codec_type,codec_name', '-of', 'compact=p=0:nk=1', url]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if p.returncode == 0:
                messagebox.showinfo('CamRec', 'Камера отвечает.\n\n' + (p.stdout.strip() or 'Поток найден.'))
            else:
                messagebox.showerror('CamRec', p.stderr[-1200:] or 'Не удалось открыть поток.')
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))

    def open_autostart_dialog(self):
        win = tk.Toplevel(self)
        win.title('Автозапуск записи')
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill='both', expand=True)
        enabled = tk.BooleanVar(value=bool(self.cfg.get('autostart_enabled', False)))
        source = tk.StringVar(value=self.cfg.get('autostart_source', 'usb'))
        ttk.Checkbutton(frm, text='Автоматически начинать запись', variable=enabled).pack(anchor='w')
        ttk.Label(frm, text='Какой источник запускать:').pack(anchor='w', pady=(12, 4))
        ttk.Radiobutton(frm, text='USB-веб-камера', variable=source, value='usb').pack(anchor='w')
        ttk.Radiobutton(frm, text='IP / RTSP камера', variable=source, value='network').pack(anchor='w')
        ttk.Label(frm, text='Работает после загрузки компьютера и при следующем запуске CamRec.\nЕсли выбрана IP-камера, заранее сохрани её URL и логин/пароль.').pack(anchor='w', pady=(12, 10))

        def save():
            if enabled.get() and source.get() == 'network' and not self.cfg.get('network_url'):
                messagebox.showwarning('CamRec', 'Сначала укажи URL IP-камеры.')
                return
            self.cfg['autostart_enabled'] = bool(enabled.get())
            self.cfg['autostart_source'] = source.get()
            write_json_inplace(CONFIG_FILE, self.cfg)
            win.destroy()
            messagebox.showinfo('CamRec', 'Настройки автозапуска сохранены.')

        row = ttk.Frame(frm)
        row.pack(fill='x', pady=(4, 0))
        ttk.Button(row, text='Сохранить', command=save).pack(side='left')
        ttk.Button(row, text='Отмена', command=win.destroy).pack(side='right')

    def apply_autostart_on_gui_launch(self):
        cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG)
        if cfg.get('autostart_enabled'):
            cfg['source_type'] = cfg.get('autostart_source', 'usb')
            cfg['status'] = 'play'
            write_json_inplace(CONFIG_FILE, cfg)
            self.cfg = cfg
            self._load_to_widgets()

    def refresh_audio_devices(self):
        devices = ['default']
        try:
            p = subprocess.run(['arecord', '-L'], capture_output=True, text=True, timeout=4)
            for line in p.stdout.splitlines():
                if line and not line[0].isspace() and line.strip() not in devices:
                    devices.append(line.strip())
        except Exception:
            pass
        self.audio_combo['values'] = devices
        if not self.audio_device_var.get():
            self.audio_device_var.set('default')

    def probe_source(self):
        self.save_settings(quiet=True)
        if self.source_var.get() == 'usb':
            dev = self.usb_var.get().strip()
            try:
                p = subprocess.run(['v4l2-ctl', '-d', dev, '--all'], capture_output=True, text=True, timeout=6)
                if p.returncode == 0:
                    msg = 'Веб-камера найдена.'
                    if self.audio_var.get():
                        a = subprocess.run(['arecord', '-D', self.audio_device_var.get(), '-d', '1', '-f', 'S16_LE', '-r', '48000', '-c', '1', '/tmp/camrec_audio_test.wav'], capture_output=True, text=True, timeout=4)
                        msg += '\nМикрофон: ' + ('работает.' if a.returncode == 0 else 'не удалось проверить.')
                    messagebox.showinfo('CamRec', msg)
                else:
                    messagebox.showerror('CamRec', p.stderr or 'Камера не отвечает.')
            except Exception as exc:
                messagebox.showerror('CamRec', str(exc))
        else:
            creds = read_json(CREDS_FILE, {'username': '', 'password': ''})
            self.probe_network(self.cfg.get('network_url', ''), creds.get('username', ''), creds.get('password', ''))

    def live(self):
        self.save_settings(quiet=True)
        try:
            if self.source_var.get() == 'usb':
                cmd = ['ffplay', '-f', 'v4l2', '-channel', '0', '-pixel_format', 'yuyv422', '-video_size', RES_MAP.get(self.res_var.get(), '640x480'), '-framerate', str(self.fps_var.get()), '-i', self.usb_var.get().strip()]
            else:
                url = authenticated_url(self.cfg.get('network_url', ''))
                cmd = ['mpv', url]
            subprocess.Popen(cmd)
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))

    def refresh_archive(self):
        selected = self.tree.selection()
        current = self.tree.item(selected[0], 'text') if selected else None
        for i in self.tree.get_children():
            self.tree.delete(i)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(ARCHIVE_DIR.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            try:
                st = p.stat()
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
                size = self.human_size(st.st_size)
                source = 'Вебка' if p.name.startswith('webcam_') else 'IP-камера' if p.name.startswith('camera_') else 'Видео'
                iid = self.tree.insert('', 'end', text=p.name, values=(stamp, size, source))
                if p.name == current:
                    self.tree.selection_set(iid)
            except OSError:
                pass

    @staticmethod
    def human_size(n):
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if n < 1024 or unit == 'TB':
                return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
            n /= 1024

    def selected_path(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('CamRec', 'Выбери запись.')
            return None
        return ARCHIVE_DIR / self.tree.item(sel[0], 'text')

    def play_selected(self):
        p = self.selected_path()
        if p and p.exists():
            player = 'mpv' if shutil.which('mpv') else 'ffplay'
            subprocess.Popen([player, str(p)])

    def export_selected(self):
        p = self.selected_path()
        if not p or not p.exists():
            return
        dest = filedialog.asksaveasfilename(initialfile=p.name, defaultextension='.mp4', filetypes=[('MP4 video', '*.mp4'), ('Все файлы', '*.*')])
        if dest:
            try:
                shutil.copy2(p, dest)
                messagebox.showinfo('CamRec', 'Файл экспортирован.')
            except Exception as exc:
                messagebox.showerror('CamRec', str(exc))

    def open_archive_folder(self):
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['xdg-open', str(ARCHIVE_DIR)])

    def tick(self):
        state = read_json(STATE_FILE, {'state': 'unknown', 'message': ''})
        labels = {'recording': '● Идёт запись', 'starting': 'Запуск…', 'stopped': 'Остановлено', 'idle': 'Готово', 'error': 'Ошибка'}
        text = labels.get(state.get('state'), state.get('state', 'Неизвестно'))
        msg = state.get('message', '')
        if msg and state.get('state') == 'error':
            text += ': ' + msg[:100]
        self.status_label.config(text='Статус: ' + text)
        self.refresh_archive()
        self.after(2000, self.tick)


if __name__ == '__main__':
    CamRecApp().mainloop()
