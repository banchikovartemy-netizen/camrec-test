import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

W, H, FPS = 640, 360, 10


def flags():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def read_exact(pipe, n):
    out = bytearray()
    while len(out) < n:
        chunk = pipe.read(n - len(out))
        if not chunk:
            return None
        out.extend(chunk)
    return bytes(out)


class PreviewWindow(tk.Toplevel):
    def __init__(self, master, url, on_close=None):
        super().__init__(master)
        self.title('CamRec — экран записи')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.url, self.cb, self.zoom = url, on_close, 1.0
        self.proc = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.frame_id = 0
        self.shown_id = -1
        self.photo = None
        self.pic = ttk.Label(self, text='Подключение к записи…', anchor='center')
        self.pic.pack(padx=6, pady=(6, 0))
        self.info = ttk.Label(self, text='ЛКМ: приблизить   •   ПКМ: отдалить   •   Zoom 1.0×')
        self.info.pack(fill='x', padx=8, pady=8)
        for widget in (self, self.pic):
            widget.bind('<Button-1>', self.zoom_in)
            widget.bind('<Button-3>', self.zoom_out)
        self.start_decoder()
        self.after(80, self.render)

    def vf(self):
        z = self.zoom
        if z <= 1.001:
            return f'scale={W}:{H}'
        return f'crop=iw/{z:.3f}:ih/{z:.3f}:(iw-iw/{z:.3f})/2:(ih-ih/{z:.3f})/2,scale={W}:{H}'

    def start_decoder(self):
        self.stop_decoder()
        self.stop_event.clear()
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-fflags', 'nobuffer', '-flags', 'low_delay',
               '-i', self.url, '-an', '-vf', self.vf(), '-r', str(FPS), '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, creationflags=flags())
            threading.Thread(target=self.reader, daemon=True).start()
        except Exception as exc:
            self.pic.config(text=f'Не удалось открыть экран записи:\n{exc}')

    def stop_decoder(self):
        self.stop_event.set()
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate(); p.wait(timeout=1)
            except Exception:
                try: p.kill()
                except Exception: pass

    def reader(self):
        p = self.proc
        if not p or not p.stdout: return
        size = W * H * 3
        while not self.stop_event.is_set() and p.poll() is None:
            frame = read_exact(p.stdout, size)
            if frame is None: break
            with self.lock:
                self.frame = frame; self.frame_id += 1

    def render(self):
        if not self.winfo_exists(): return
        frame = None; fid = None
        with self.lock:
            if self.frame is not None and self.frame_id != self.shown_id:
                frame, fid = self.frame, self.frame_id
        if frame is not None:
            try:
                ppm = f'P6\n{W} {H}\n255\n'.encode() + frame
                self.photo = tk.PhotoImage(data=ppm, format='PPM')
                self.pic.configure(image=self.photo, text='')
                self.shown_id = fid
            except tk.TclError: pass
        self.after(80, self.render)

    def change_zoom(self, delta):
        self.zoom = max(1.0, min(4.0, round(self.zoom + delta, 1)))
        self.info.config(text=f'ЛКМ: приблизить   •   ПКМ: отдалить   •   Zoom {self.zoom:.1f}×')
        self.start_decoder()
        return 'break'

    def zoom_in(self, event=None): return self.change_zoom(0.2)
    def zoom_out(self, event=None): return self.change_zoom(-0.2)

    def close(self):
        self.stop_decoder()
        cb, self.cb = self.cb, None
        self.destroy()
        if cb: cb()
