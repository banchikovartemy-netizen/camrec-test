import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from tkinter import filedialog, messagebox
from camrec_preview import PreviewWindow

DATA_DIR = Path('/var/lib/camrec')
ARCHIVE_DIR = DATA_DIR / 'archive'
CONFIG_FILE = DATA_DIR / 'settings.json'
STATE_FILE = Path('/run/camrec/state.json')
PREVIEW_URL = 'udp://127.0.0.1:23001?fifo_size=1000000&overrun_nonfatal=1'


def read_json(path, default):
    try:
        with path.open('r', encoding='utf-8') as f: v = json.load(f)
        return v if isinstance(v, dict) else default.copy()
    except Exception: return default.copy()


def write_json(path, data):
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())


class LinuxActionsMixin:
    def open_recording_preview(self):
        state = read_json(STATE_FILE, {'state': 'unknown'})
        if state.get('state') not in ('recording', 'starting'):
            cfg = read_json(CONFIG_FILE, {})
            if cfg.get('status') != 'play':
                messagebox.showinfo('CamRec', 'Сначала начни запись.'); return
        cfg = read_json(CONFIG_FILE, {})
        if not cfg.get('preview_enabled'):
            cfg['preview_enabled'] = True; write_json(CONFIG_FILE, cfg); self.cfg = cfg
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.deiconify(); self.preview_window.lift(); return
        self.preview_window = PreviewWindow(self, PREVIEW_URL, on_close=self.preview_closed_by_user)

    def preview_closed_by_user(self):
        self.preview_window = None
        cfg = read_json(CONFIG_FILE, {})
        if cfg.get('preview_enabled'):
            cfg['preview_enabled'] = False; write_json(CONFIG_FILE, cfg); self.cfg = cfg

    def close_preview_window(self, update_config=True):
        win, self.preview_window = self.preview_window, None
        if win and win.winfo_exists():
            win.cb = None; win.stop_decoder(); win.destroy()
        if update_config:
            cfg = read_json(CONFIG_FILE, {})
            cfg['preview_enabled'] = False; write_json(CONFIG_FILE, cfg); self.cfg = cfg

    def refresh_archive(self):
        selected = self.tree.selection()
        current = self.tree.item(selected[0], 'text') if selected else None
        for i in self.tree.get_children(): self.tree.delete(i)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(ARCHIVE_DIR.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            try:
                st = p.stat(); stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
                source = 'Вебка' if p.name.startswith('webcam_') else 'IP-камера' if p.name.startswith('camera_') else 'Видео'
                iid = self.tree.insert('', 'end', text=p.name, values=(stamp, self.human_size(st.st_size), source))
                if p.name == current: self.tree.selection_set(iid)
            except OSError: pass

    @staticmethod
    def human_size(n):
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if n < 1024 or unit == 'TB': return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
            n /= 1024

    def selected_path(self, quiet=False):
        sel = self.tree.selection()
        if not sel:
            if not quiet: messagebox.showinfo('CamRec', 'Выбери запись.')
            return None
        return ARCHIVE_DIR / self.tree.item(sel[0], 'text')

    def newest_recording(self):
        files = [p for p in ARCHIVE_DIR.glob('*.mp4') if p.is_file()]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def play_selected(self):
        p = self.selected_path()
        if p and p.exists():
            subprocess.Popen([('mpv' if shutil.which('mpv') else 'ffplay'), str(p)])

    def choose_saved_dir(self):
        initial = self.saved_dir_var.get().strip() or str(self.default_saved_dir())
        chosen = filedialog.askdirectory(initialdir=initial if Path(initial).exists() else str(Path.home()))
        if chosen: self.saved_dir_var.set(chosen); self.save_settings(quiet=True)

    def save_recording(self):
        state = read_json(STATE_FILE, {'state': 'unknown'})
        if state.get('state') in ('recording', 'starting'):
            if not messagebox.askyesno('CamRec', 'Запись ещё идёт. Остановить её и сохранить последний файл?'): return
            self.stop_recording(); self.after(500, lambda: self._wait_and_save_latest(0)); return
        self._save_after_stop(False)

    def _wait_and_save_latest(self, attempt):
        state = read_json(STATE_FILE, {'state': 'unknown'})
        if state.get('state') not in ('stopped', 'idle', 'error') and attempt < 20:
            self.after(500, lambda: self._wait_and_save_latest(attempt + 1)); return
        self._save_after_stop(True)

    def _save_after_stop(self, prefer_newest=False):
        self.refresh_archive()
        src = self.newest_recording() if prefer_newest else (self.selected_path(True) or self.newest_recording())
        if not src or not src.exists(): messagebox.showwarning('CamRec', 'Готовых записей пока нет.'); return
        dest_dir = Path(self.saved_dir_var.get().strip() or self.default_saved_dir()).expanduser()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True); dest = dest_dir / src.name; i = 1
            while dest.exists(): dest = dest_dir / f'{src.stem}_{i}{src.suffix}'; i += 1
            shutil.copy2(src, dest); self.saved_dir_var.set(str(dest_dir)); self.save_settings(quiet=True)
            messagebox.showinfo('CamRec', f'Запись сохранена:\n{dest}')
        except Exception as exc: messagebox.showerror('CamRec', f'Не удалось сохранить запись:\n{exc}')

    def export_selected(self):
        p = self.selected_path()
        if not p or not p.exists(): return
        dest = filedialog.asksaveasfilename(initialfile=p.name, defaultextension='.mp4', filetypes=[('MP4 video', '*.mp4'), ('Все файлы', '*.*')])
        if dest:
            try: shutil.copy2(p, dest); messagebox.showinfo('CamRec', 'Файл экспортирован.')
            except Exception as exc: messagebox.showerror('CamRec', str(exc))

    def open_archive_folder(self):
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True); subprocess.Popen(['xdg-open', str(ARCHIVE_DIR)])

    def open_saved_folder(self):
        p = Path(self.saved_dir_var.get().strip() or self.default_saved_dir()).expanduser(); p.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['xdg-open', str(p)])
