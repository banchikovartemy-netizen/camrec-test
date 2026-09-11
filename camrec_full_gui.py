#!/usr/bin/env python3
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from camrec_gui import CamRecApp, CONFIG_FILE, DEFAULT_CONFIG, read_json, write_json_inplace
from camrec_actions import LinuxActionsMixin


class FullCamRec(LinuxActionsMixin, CamRecApp):
    def __init__(self):
        self.preview_window = None
        super().__init__()

    def default_saved_dir(self):
        return Path.home() / 'Videos' / 'CamRec' / 'Saved'

    def _build(self):
        super()._build()
        extra = ttk.LabelFrame(self, text='Сохранение и экран записи', padding=8)
        extra.pack(fill='x', padx=12, pady=(0, 8))
        self.saved_dir_var = tk.StringVar(value='')
        ttk.Label(extra, text='Папка для готовых записей:').pack(side='left')
        ttk.Entry(extra, textvariable=self.saved_dir_var, width=42).pack(side='left', padx=6, fill='x', expand=True)
        ttk.Button(extra, text='Выбрать…', command=self.choose_saved_dir).pack(side='left', padx=4)
        ttk.Button(extra, text='💾 Сохранить запись', command=self.save_recording).pack(side='left', padx=4)
        ttk.Button(extra, text='Экран записи', command=self.open_recording_preview).pack(side='left', padx=4)

    def _load_to_widgets(self):
        super()._load_to_widgets()
        if hasattr(self, 'saved_dir_var'):
            self.saved_dir_var.set(self.cfg.get('saved_dir', '') or str(self.default_saved_dir()))

    def collect_settings(self):
        cfg = super().collect_settings()
        if hasattr(self, 'saved_dir_var'):
            cfg['saved_dir'] = self.saved_dir_var.get().strip() or str(self.default_saved_dir())
        cfg.setdefault('preview_enabled', False)
        return cfg

    def set_status(self, value, preview=None):
        if not self.save_settings(quiet=True):
            return False
        cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG)
        cfg['status'] = value
        if preview is not None:
            cfg['preview_enabled'] = bool(preview)
        write_json_inplace(CONFIG_FILE, cfg)
        self.cfg = cfg
        return True

    def start_recording(self):
        self.collect_settings()
        if self.source_var.get() == 'network' and not self.cfg.get('network_url'):
            messagebox.showwarning('CamRec', 'Сначала подключи IP-камеру и укажи RTSP/HTTP URL.')
            return
        if self.set_status('play', preview=True):
            self.after(900, self.open_recording_preview)

    def stop_recording(self):
        self.set_status('stop', preview=False)
        self.close_preview_window(update_config=False)

    def apply_autostart_on_gui_launch(self):
        cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG)
        if cfg.get('autostart_enabled'):
            cfg['source_type'] = cfg.get('autostart_source', 'usb')
            cfg['status'] = 'play'
            cfg['preview_enabled'] = True
            write_json_inplace(CONFIG_FILE, cfg)
            self.cfg = cfg
            self._load_to_widgets()
            self.after(900, self.open_recording_preview)

    def on_close(self):
        self.close_preview_window(update_config=True)
        self.destroy()


if __name__ == '__main__':
    FullCamRec().mainloop()
