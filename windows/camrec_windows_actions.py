import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote, urlsplit, urlunsplit
from camrec_preview import PreviewWindow

APP_NAME = 'CamRec'
DATA_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / APP_NAME
ARCHIVE_DIR = DATA_DIR / 'archive'
CONFIG_FILE = DATA_DIR / 'settings.json'
CREDS_FILE = DATA_DIR / 'credentials.json'
PREVIEW_URL = 'udp://127.0.0.1:23002?fifo_size=1000000&overrun_nonfatal=1'


def read_json(path, default):
    try:
        with path.open('r', encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else default.copy()
    except Exception:
        return default.copy()


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def autorun_command():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    py = Path(sys.executable)
    pw = py.with_name('pythonw.exe')
    exe = pw if pw.exists() else py
    return f'"{exe}" "{Path(sys.argv[0]).resolve()}"'


def set_windows_run(enabled):
    import winreg
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autorun_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


class WindowsActionsMixin:
    def open_ip_dialog(self):
        win = self._modal('Подключение к IP-камере')
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill='both', expand=True)
        creds = read_json(CREDS_FILE, {'username': '', 'password': ''})
        url = self.tk_str(self.cfg.get('network_url', ''))
        user = self.tk_str(creds.get('username', ''))
        pwv = self.tk_str(creds.get('password', ''))
        show = self.tk_bool(False)
        ttk.Label(frm, text='RTSP / HTTP URL:').grid(row=0, column=0, sticky='e', pady=5)
        ttk.Entry(frm, textvariable=url, width=52).grid(row=0, column=1, padx=6)
        ttk.Label(frm, text='Логин:').grid(row=1, column=0, sticky='e', pady=5)
        ttk.Entry(frm, textvariable=user, width=34).grid(row=1, column=1, sticky='w', padx=6)
        ttk.Label(frm, text='Пароль:').grid(row=2, column=0, sticky='e', pady=5)
        pw = ttk.Entry(frm, textvariable=pwv, show='●', width=34)
        pw.grid(row=2, column=1, sticky='w', padx=6)
        ttk.Checkbutton(frm, text='Показать пароль', variable=show, command=lambda: pw.config(show='' if show.get() else '●')).grid(row=3, column=1, sticky='w', padx=6)
        ttk.Label(frm, text='При включённом звуке CamRec возьмёт аудиодорожку из потока, если камера её отдаёт.').grid(row=4, column=0, columnspan=2, sticky='w', pady=(8, 10))

        def save():
            self.cfg['network_url'] = url.get().strip()
            write_json(CREDS_FILE, {'username': user.get(), 'password': pwv.get()})
            write_json(CONFIG_FILE, self.cfg)
            self.source_var.set('network')
            self.update_network_label()
            self.source_changed()
            win.destroy()

        ttk.Button(frm, text='Проверить', command=lambda: self.probe_network(url.get(), user.get(), pwv.get())).grid(row=5, column=0)
        ttk.Button(frm, text='Сохранить', command=save).grid(row=5, column=1, sticky='w', padx=6)
        ttk.Button(frm, text='Отмена', command=win.destroy).grid(row=5, column=1, sticky='e', padx=6)

    def probe_network(self, url, username, password):
        url = url.strip()
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
            p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=index,codec_type,codec_name', '-of', 'compact=p=0:nk=1', url], capture_output=True, text=True, timeout=12, creationflags=self.no_window_flags())
            if p.returncode == 0:
                messagebox.showinfo('CamRec', 'Камера отвечает.\n\n' + (p.stdout.strip() or 'Поток найден.'))
            else:
                messagebox.showerror('CamRec', p.stderr[-1200:] or 'Не удалось открыть поток.')
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))

    def open_autostart_dialog(self):
        win = self._modal('Автозапуск')
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill='both', expand=True)
        enabled = self.tk_bool(bool(self.cfg.get('autostart_enabled', False)))
        source = self.tk_str(self.cfg.get('autostart_source', 'usb'))
        launch = self.tk_bool(bool(self.cfg.get('launch_with_windows', False)))
        ttk.Checkbutton(frm, text='Автоматически начинать запись при запуске CamRec', variable=enabled).pack(anchor='w')
        ttk.Label(frm, text='Источник:').pack(anchor='w', pady=(10, 4))
        ttk.Radiobutton(frm, text='Веб-камера', variable=source, value='usb').pack(anchor='w')
        ttk.Radiobutton(frm, text='IP / RTSP камера', variable=source, value='network').pack(anchor='w')
        ttk.Checkbutton(frm, text='Запускать CamRec вместе с Windows', variable=launch).pack(anchor='w', pady=(12, 4))
        ttk.Label(frm, text='Если обе галочки включены, после входа в Windows CamRec откроется и сразу начнёт запись.').pack(anchor='w', pady=(4, 10))

        def save():
            if enabled.get() and source.get() == 'network' and not self.cfg.get('network_url'):
                messagebox.showwarning('CamRec', 'Сначала укажи URL IP-камеры.')
                return
            self.cfg['autostart_enabled'] = bool(enabled.get())
            self.cfg['autostart_source'] = source.get()
            self.cfg['launch_with_windows'] = bool(launch.get())
            write_json(CONFIG_FILE, self.cfg)
            try:
                set_windows_run(bool(launch.get()))
            except Exception as exc:
                messagebox.showwarning('CamRec', f'Настройки записи сохранены, но автозапуск Windows изменить не удалось:\n{exc}')
            win.destroy()

        row = ttk.Frame(frm)
        row.pack(fill='x')
        ttk.Button(row, text='Сохранить', command=save).pack(side='left')
        ttk.Button(row, text='Отмена', command=win.destroy).pack(side='right')

    def apply_autostart(self):
        cfg = read_json(CONFIG_FILE, self.default_config)
        for k, v in self.default_config.items():
            cfg.setdefault(k, v)
        self.cfg = cfg
        if cfg.get('autostart_enabled'):
            self.source_var.set(cfg.get('autostart_source', 'usb'))
            self.source_changed()
            self.start_recording()

    def open_recording_preview(self):
        if not self.rec_proc or self.rec_proc.poll() is not None:
            messagebox.showinfo('CamRec', 'Сначала начни запись.')
            return
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.lift()
            return
        self.preview_window = PreviewWindow(self, PREVIEW_URL, on_close=self.preview_closed)

    def preview_closed(self):
        self.preview_window = None

    def close_preview_window(self):
        win, self.preview_window = self.preview_window, None
        if win and win.winfo_exists():
            win.cb = None
            win.stop_decoder()
            win.destroy()

    def refresh_archive(self):
        current = None
        sel = self.tree.selection()
        if sel:
            current = self.tree.item(sel[0], 'text')
        for i in self.tree.get_children():
            self.tree.delete(i)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for p in sorted(ARCHIVE_DIR.glob('*.mp4'), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                st = p.stat()
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
                source = 'Вебка' if p.name.startswith('webcam_') else 'IP-камера' if p.name.startswith('camera_') else 'Видео'
                iid = self.tree.insert('', 'end', text=p.name, values=(stamp, self.human_size(st.st_size), source))
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

    def selected_path(self, quiet=False):
        sel = self.tree.selection()
        if not sel:
            if not quiet:
                messagebox.showinfo('CamRec', 'Выбери запись.')
            return None
        return ARCHIVE_DIR / self.tree.item(sel[0], 'text')

    def newest_recording(self):
        files = list(ARCHIVE_DIR.glob('*.mp4'))
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def play_selected(self):
        p = self.selected_path()
        if p and p.exists():
            subprocess.Popen(['ffplay', '-autoexit', str(p)], creationflags=self.no_window_flags())

    def choose_saved_dir(self):
        initial = self.saved_dir_var.get().strip() or str(self.default_saved_dir())
        chosen = filedialog.askdirectory(initialdir=initial if Path(initial).exists() else str(Path.home()))
        if chosen:
            self.saved_dir_var.set(chosen)
            self.save_settings(quiet=True)

    def save_recording(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            if not messagebox.askyesno('CamRec', 'Запись ещё идёт. Остановить её и сохранить последний файл?'):
                return
            self.stop_recording()
            latest = True
        else:
            latest = False
        self.refresh_archive()
        src = self.newest_recording() if latest else (self.selected_path(True) or self.newest_recording())
        if not src or not src.exists():
            messagebox.showwarning('CamRec', 'Готовых записей пока нет.')
            return
        dest_dir = Path(self.saved_dir_var.get().strip() or self.default_saved_dir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        i = 1
        while dest.exists():
            dest = dest_dir / f'{src.stem}_{i}{src.suffix}'
            i += 1
        try:
            shutil.copy2(src, dest)
            self.saved_dir_var.set(str(dest_dir))
            self.save_settings(quiet=True)
            messagebox.showinfo('CamRec', f'Запись сохранена:\n{dest}')
        except Exception as exc:
            messagebox.showerror('CamRec', str(exc))

    def export_selected(self):
        p = self.selected_path()
        if not p or not p.exists():
            return
        dest = filedialog.asksaveasfilename(initialfile=p.name, defaultextension='.mp4', filetypes=[('MP4 video', '*.mp4'), ('Все файлы', '*.*')])
        if dest:
            shutil.copy2(p, dest)

    def open_saved_folder(self):
        p = Path(self.saved_dir_var.get().strip() or self.default_saved_dir())
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)

    def cleanup_old(self):
        cutoff = time.time() - int(self.cfg.get('retention_days', 7)) * 86400
        for p in ARCHIVE_DIR.glob('*.mp4'):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
