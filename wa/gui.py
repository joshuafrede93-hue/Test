import queue
import threading
import tkinter as tk

import customtkinter as ctk

import os
import sys

from . import APP_TITLE, __version__, winui
from .core import UI
from .lmstudio_setup import Setup, SetupError
from .voice import Listener, VoiceError


class GuiBridge(UI):
    """Dipanggil dari thread pekerja; semua operasi tampilan dikirim lewat antrian ke thread utama."""

    def __init__(self, q, stop_event):
        self.q = q
        self.stop_event = stop_event

    def log(self, msg):
        self.q.put(("log", msg))

    def _blocking(self, kind, text):
        ev = threading.Event()
        holder = [None]
        self.q.put((kind, text, ev, holder))
        while not ev.wait(0.3):
            if self.stop_event.is_set():
                return None
        return holder[0]

    def confirm(self, msg):
        return bool(self._blocking("confirm", msg))

    def ask(self, question):
        return self._blocking("ask", question)

    def should_stop(self):
        return self.stop_event.is_set()


PALETTE = {
    "bg": "#0f1115", "panel": "#171a21", "card": "#1e222b", "line": "#2a2f3a",
    "text": "#e6e8ee", "muted": "#8b93a7", "accent": "#4f8cff", "accent_hover": "#3b76e6",
    "ok": "#34d399", "warn": "#fbbf24", "bad": "#f87171",
}
FONT = "Segoe UI"


class Tooltip:
    """Keterangan kecil saat kursor di atas tombol ikon."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, bg="#2a2f3a", fg="#e6e8ee", padx=8, pady=4, font=(FONT, 10)).pack()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() - 34
        tip.geometry("+%d+%d" % (x, max(0, y)))
        self.tip = tip

    def _hide(self, _e=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class Dialog(ctk.CTkToplevel):
    """Dialog modern: tanya ya/tidak atau isian teks. Muncul sendiri di paling depan layar."""

    def __init__(self, parent, title, text, kind="yesno", initial="", yes="Ya", no="Tidak", header=None):
        super().__init__(parent)
        self.result = None
        self.kind = kind
        self.title(title)
        self.configure(fg_color=PALETTE["panel"])
        self.resizable(False, False)
        self.attributes("-topmost", True)
        if header:
            bar = ctk.CTkFrame(self, fg_color=PALETTE["warn"], corner_radius=0, height=34)
            bar.pack(fill="x")
            ctk.CTkLabel(bar, text=header, font=(FONT, 13, "bold"), text_color="#1b1b1b").pack(
                side="left", padx=18, pady=5)
        ctk.CTkLabel(self, text=text, justify="left", wraplength=420, font=(FONT, 13),
                     text_color=PALETTE["text"]).pack(padx=22, pady=(18, 12), anchor="w")
        self.entry = None
        if kind == "text":
            self.entry = ctk.CTkEntry(self, width=420, height=36, font=(FONT, 13))
            self.entry.insert(0, initial)
            self.entry.pack(padx=22, pady=(0, 12))
            self.entry.bind("<Return>", lambda e: self._ok())
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=22, pady=(4, 18), anchor="e")
        ctk.CTkButton(row, text=no if kind == "yesno" else "Batal", width=96, height=34, corner_radius=10,
                      fg_color=PALETTE["card"], hover_color=PALETTE["line"], command=self._cancel).pack(side="left", padx=6)
        ctk.CTkButton(row, text=yes if kind == "yesno" else "OK", width=96, height=34, corner_radius=10,
                      fg_color=PALETTE["accent"], hover_color=PALETTE["accent_hover"], command=self._ok).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        # Tengah layar (bukan tengah jendela utama, yang mungkin sedang diciutkan).
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        # CTkToplevel baru siap sesaat setelah dibuat; dorong ke depan beberapa kali sampai benar-benar terlihat.
        for ms in (30, 250, 700):
            self.after(ms, self._front)
        self.after(60, self._grab)

    def _front(self):
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            winui.force_foreground(self)
            winui.flash(self)
            self.focus_force()
            if self.entry is not None:
                self.entry.focus_set()
            if not self.winfo_viewable():  # cadangan: kalau tetap tersembunyi, tampilkan jendela utama
                self.master.deiconify()
        except tk.TclError:
            pass

    def _grab(self):
        try:
            self.grab_set()
        except tk.TclError:
            self.after(100, self._grab)

    def _ok(self):
        self.result = True if self.kind == "yesno" else (self.entry.get() if self.entry else "")
        self.destroy()

    def _cancel(self):
        self.result = False if self.kind == "yesno" else None
        self.destroy()


class App:
    def __init__(self, root, cfg, memory, llm, make_agent, config_path="config.json", base_dir=".", auto_checks=True):
        self.root = root
        self.config_path = config_path
        self.base_dir = base_dir
        self.busy_setup = False
        self.agent_running = False
        self.cfg = cfg
        self.memory = memory
        self.llm = llm
        self.make_agent = make_agent  # fungsi(ui) -> Agent
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.overlay = None
        self.overlay_label = None
        self.overlay_title = None
        self.current_goal = ""
        self.listening = False
        self.voice_noted = False
        self.voice_cfg = cfg.get("voice", {})

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        root.title(APP_TITLE)
        root.geometry("980x640")
        root.minsize(820, 520)
        root.configure(fg_color=PALETTE["bg"])
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)

        # ----- sidebar -----
        side = ctk.CTkFrame(root, width=230, corner_radius=0, fg_color=PALETTE["panel"])
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        ctk.CTkLabel(side, text="Asisten PC", font=(FONT, 22, "bold"), text_color=PALETTE["text"]).pack(
            anchor="w", padx=20, pady=(24, 0))
        ctk.CTkLabel(side, text="Versi %s" % __version__, font=(FONT, 12), text_color=PALETTE["muted"]).pack(
            anchor="w", padx=20, pady=(0, 18))
        self.status_chip = ctk.CTkLabel(side, text="  Memeriksa model...  ", font=(FONT, 12), corner_radius=12,
                                        fg_color=PALETTE["card"], text_color=PALETTE["muted"])
        self.status_chip.pack(anchor="w", padx=20, pady=(0, 18))

        def nav(text, cmd, primary=False):
            ctk.CTkButton(
                side, text=text, command=cmd, height=40, corner_radius=10, anchor="w", font=(FONT, 13),
                fg_color=PALETTE["accent"] if primary else "transparent",
                hover_color=PALETTE["accent_hover"] if primary else PALETTE["card"],
                text_color="white" if primary else PALETTE["text"]).pack(fill="x", padx=14, pady=3)

        nav("Setup otomatis", self.start_setup, primary=True)
        nav("Tes koneksi model", self.test_model)
        nav("Ajari AI", self.teach)
        nav("Skill tersimpan", self.show_skills)
        nav("Fakta tentang saya", self.add_fact)
        ctk.CTkLabel(side, text="F9 = perintah suara\nF12 = hentikan AI", font=(FONT, 11), justify="left",
                     text_color=PALETTE["muted"]).pack(side="bottom", anchor="w", padx=20, pady=16)

        # ----- area utama -----
        main = ctk.CTkFrame(root, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=20, pady=18)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(main, text="Apa yang ingin dikerjakan?", font=(FONT, 20, "bold"),
                     text_color=PALETTE["text"]).grid(row=0, column=0, sticky="w", pady=(0, 10))

        self.log_box = ctk.CTkTextbox(main, corner_radius=14, fg_color=PALETTE["card"], border_width=1,
                                      border_color=PALETTE["line"], font=(FONT, 13), wrap="word",
                                      text_color=PALETTE["text"])
        self.log_box.grid(row=1, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")
        t = self.log_box._textbox
        t.tag_config("task", foreground=PALETTE["accent"], font=(FONT, 13, "bold"))
        t.tag_config("ok", foreground=PALETTE["ok"], font=(FONT, 13, "bold"))
        t.tag_config("bad", foreground=PALETTE["bad"], font=(FONT, 13, "bold"))
        t.tag_config("warn", foreground=PALETTE["warn"])
        t.tag_config("muted", foreground=PALETTE["muted"])

        bar = ctk.CTkFrame(main, fg_color=PALETTE["card"], corner_radius=14, border_width=1, border_color=PALETTE["line"])
        bar.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        bar.grid_columnconfigure(0, weight=1)
        self.goal_var = tk.StringVar()
        self.entry = ctk.CTkEntry(bar, textvariable=self.goal_var, height=44, border_width=0, fg_color="transparent",
                                  font=(FONT, 14), placeholder_text="Contoh: buka Excel dan buat tabel pemasukan dan pengeluaran")
        self.entry.grid(row=0, column=0, sticky="ew", padx=(14, 6), pady=6)
        self.entry.bind("<Return>", lambda e: self.start())
        from .icons import make_icon

        def icon(name, color="#ffffff"):
            img = make_icon(name, color, 64)
            return ctk.CTkImage(light_image=img, dark_image=img, size=(20, 20))

        self.icon_mic, self.icon_mic_on = icon("mic", PALETTE["muted"]), icon("mic", "#ffffff")
        self.mic_btn = ctk.CTkButton(bar, text="", image=self.icon_mic, width=40, height=40, corner_radius=10,
                                     fg_color=PALETTE["panel"], hover_color=PALETTE["line"], command=self.toggle_voice)
        self.mic_btn.grid(row=0, column=1, padx=(2, 4))
        self.stop_btn = ctk.CTkButton(bar, text="", image=icon("stop"), width=40, height=40, corner_radius=10,
                                      state="disabled", fg_color=PALETTE["bad"], hover_color="#dc5a5a",
                                      command=self.stop)
        self.stop_btn.grid(row=0, column=2, padx=4)
        self.run_btn = ctk.CTkButton(bar, text="", image=icon("play"), width=40, height=40, corner_radius=10,
                                     fg_color=PALETTE["accent"], hover_color=PALETTE["accent_hover"], command=self.start)
        self.run_btn.grid(row=0, column=3, padx=(4, 10))
        Tooltip(self.mic_btn, "Perintah suara (F9)")
        Tooltip(self.stop_btn, "Hentikan (F12)")
        Tooltip(self.run_btn, "Jalankan (Enter)")

        self._setup_hotkey()
        self.write("Asisten PC versi %s. Tulis atau ucapkan tugas (tombol mikrofon / F9), lalu jalankan. "
                   "Saat AI bekerja, jendela ini mengecil; tekan F12 untuk berhenti." % __version__, "muted")
        self.root.after(100, self._poll)
        self.root.after(300, self._refresh_status)
        if auto_checks:
            self.root.after(1500, self._startup_checks)

    # ---------- util tampilan ----------
    def write(self, msg, tag=None):
        if tag is None:
            low = msg.lower()
            if msg.startswith("== Tugas") or msg.startswith("== Setup"):
                tag = "task"
            elif msg.startswith("== SELESAI") or low.startswith("setup selesai") or "terhubung. model" in low:
                tag = "ok"
            elif msg.startswith("== GAGAL") or "gagal" in low[:40] or "tidak terhubung" in low:
                tag = "bad"
            elif low.startswith(("update tersedia", "catatan", "setup berhenti")):
                tag = "warn"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n", tag) if tag else self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        if self.overlay_label is not None:
            self.overlay_label.configure(text=msg[:60])

    def _set_status(self, ok, text):
        self.status_chip.configure(text="  %s  " % text, text_color=PALETTE["ok"] if ok else PALETTE["bad"])

    def _refresh_status(self):
        def work():
            ok, info = self.llm.ping()
            label = ("Model siap" if ok and "belum ada model" not in info else
                     "Model belum dimuat" if ok else "Model offline")
            self.q.put(("call", lambda: self._set_status(ok and "belum ada model" not in info, label)))
        threading.Thread(target=work, daemon=True).start()

    def _setup_hotkey(self):
        key = self.cfg["agent"].get("stop_hotkey", "f12")
        try:
            import keyboard
            keyboard.add_hotkey(key, self.stop_event.set)
        except Exception as e:
            self.write("Catatan: tombol darurat %s tidak aktif (%s). Gunakan tombol Stop." % (key.upper(), e), "warn")
            return
        vkey = self.voice_cfg.get("hotkey", "f9")
        if self.voice_cfg.get("enabled", True) and vkey:
            try:
                # Dipanggil dari thread lain: lewat antrian agar aman bagi Tkinter.
                keyboard.add_hotkey(vkey, lambda: self.q.put(("call", self.toggle_voice)))
            except Exception:
                pass

    def _make_overlay(self, title="AI sedang bekerja  -  F12 untuk berhenti", color=None):
        if self.overlay is not None:
            self._close_overlay()
        ov = ctk.CTkToplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.configure(fg_color=PALETTE["panel"])
        w, h = 400, 70
        x = ov.winfo_screenwidth() - w - 20
        y = ov.winfo_screenheight() - h - 70
        ov.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.overlay_title = ctk.CTkLabel(ov, text=title, font=(FONT, 12, "bold"),
                                          text_color=color or PALETTE["warn"])
        self.overlay_title.pack(anchor="w", padx=14, pady=(10, 0))
        self.overlay_label = ctk.CTkLabel(ov, text="", font=(FONT, 12), text_color=PALETTE["text"], anchor="w")
        self.overlay_label.pack(fill="x", padx=14)
        self.overlay = ov

    def _close_overlay(self):
        if self.overlay is not None:
            self.overlay.destroy()
        self.overlay = None
        self.overlay_label = None
        self.overlay_title = None

    def _alert(self, title, body):
        """Minta perhatian: bunyi, notifikasi Windows, dan papan kecil di pojok layar."""
        winui.beep()
        winui.notify(title, body)
        if self.overlay is not None and self.overlay_title is not None:
            self.overlay_title.configure(text=title, text_color=PALETTE["warn"])
            self.overlay_label.configure(text=body[:60])

    # ---------- dialog ----------
    def _yesno(self, text, title=None, header=None):
        d = Dialog(self.root, title or APP_TITLE, text, "yesno", header=header)
        self.root.wait_window(d)
        return bool(d.result)

    def _text(self, text, initial="", title=None, header=None):
        d = Dialog(self.root, title or APP_TITLE, text, "text", initial, header=header)
        self.root.wait_window(d)
        return d.result

    # ---------- jalankan tugas ----------
    def start(self):
        goal = self.goal_var.get().strip()
        if not goal or (self.worker and self.worker.is_alive()):
            return
        if self.busy_setup:
            self.write("Setup otomatis masih berjalan. Tunggu sampai selesai.")
            return
        self.agent_running = True
        self.current_goal = goal
        self.stop_event.clear()
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.write("== Tugas: " + goal)
        self._make_overlay()
        self.root.iconify()
        self.worker = threading.Thread(target=self._work, args=(goal,), daemon=True)
        self.worker.start()

    def _work(self, goal):
        bridge = GuiBridge(self.q, self.stop_event)
        try:
            agent = self.make_agent(bridge)
            init = getattr(agent.env, "thread_init", None)
            if init:
                init()
            result = agent.run(goal)
        except Exception as e:  # jangan biarkan thread mati diam-diam
            from .core import Result
            result = Result("failed", "Error tak terduga: %s" % e)
        self.q.put(("finished", result))

    def stop(self):
        self.stop_event.set()
        self.write("Menghentikan...")

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.write(item[1])
                elif kind == "confirm":
                    self._dialog_confirm(*item[1:])
                elif kind == "ask":
                    self._dialog_ask(*item[1:])
                elif kind == "finished":
                    self._finished(item[1])
                elif kind == "call":
                    item[1]()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _dialog_confirm(self, text, ev, holder):
        self._alert("AI butuh persetujuan Anda", text.split("\n")[1] if "\n" in text else text)
        holder[0] = self._yesno(text, APP_TITLE + ": konfirmasi", header="Butuh persetujuan Anda")
        self._after_dialog()
        ev.set()

    def _dialog_ask(self, text, ev, holder):
        self._alert("AI bertanya kepada Anda", text)
        holder[0] = self._text(text, "", APP_TITLE + ": AI bertanya", header="AI butuh jawaban Anda")
        self._after_dialog()
        ev.set()

    def _after_dialog(self):
        if self.overlay is not None and self.overlay_title is not None:
            self.overlay_title.configure(text="AI sedang bekerja  -  F12 untuk berhenti", text_color=PALETTE["warn"])

    def _finished(self, result):
        self.agent_running = False
        self._close_overlay()
        self.root.deiconify()
        self.root.lift()
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        labels = {"done": "SELESAI", "failed": "GAGAL", "stopped": "DIHENTIKAN", "max_steps": "BATAS LANGKAH"}
        self.write("== %s: %s" % (labels.get(result.status, result.status), result.summary))
        winui.notify("Asisten PC: %s" % labels.get(result.status, result.status).capitalize(), result.summary)
        if result.status == "done" and result.executed:
            name = self._text(
                "Tugas selesai. Simpan langkah-langkahnya sebagai skill?\n"
                "Skill bisa diputar ulang tanpa AI (lebih cepat).\n\n"
                "Isi nama skill, atau kosongkan untuk tidak menyimpan:", self.current_goal[:40])
            if name and self.memory.save_skill(name, self.current_goal, result.executed):
                self.write('Skill "%s" tersimpan (%d langkah).' % (name, len(result.executed)))

    # ---------- suara ----------
    def toggle_voice(self):
        if not self.voice_cfg.get("enabled", True):
            return
        if self.listening:
            self.stop_event.set()  # klik lagi / F12 = batalkan rekaman
            return
        if self.agent_running or self.busy_setup:
            self.write("Perintah suara tidak bisa dipakai saat AI sedang bekerja atau setup berjalan.", "warn")
            return
        self.listening = True
        self.stop_event.clear()
        self.mic_btn.configure(image=self.icon_mic_on, fg_color=PALETTE["bad"], hover_color="#dc5a5a")
        self.run_btn.configure(state="disabled")
        if not self.voice_noted:
            self.write("Catatan: suara Anda dikirim ke layanan pengenalan suara Google (butuh internet) hanya untuk "
                       "diubah jadi teks.", "muted")
            self.voice_noted = True
        self.write("Mendengarkan... ucapkan perintah Anda.", "task")
        self._make_overlay("Mendengarkan perintah suara...", PALETTE["bad"])
        winui.beep("tick")
        threading.Thread(target=self._voice_worker, daemon=True).start()

    def _voice_worker(self):
        text, error = "", None
        try:
            lst = Listener(self.voice_cfg)
            text = lst.listen(should_stop=self._voice_stop,
                              on_status=lambda s: self.q.put(("call", lambda: self._voice_status(s))))
        except VoiceError as e:
            error = str(e)
        except Exception as e:
            error = "Perintah suara gagal: %s" % e
        self.q.put(("call", lambda: self._voice_done(text, error)))

    def _voice_stop(self):
        return self.stop_event.is_set()

    def _voice_status(self, status):
        if self.overlay_title is not None and status == "recognizing":
            self.overlay_title.configure(text="Mengenali ucapan...", text_color=PALETTE["warn"])

    def _voice_done(self, text, error):
        self.listening = False
        self.stop_event.clear()
        self.mic_btn.configure(image=self.icon_mic, fg_color=PALETTE["panel"], hover_color=PALETTE["line"])
        if not self.agent_running:
            self.run_btn.configure(state="normal")
            self._close_overlay()
        if error:
            self.write("Suara gagal: " + error, "bad")
            return
        if not text:
            self.write("Tidak ada ucapan yang terdengar jelas. Coba lagi (klik mikrofon atau F9).", "warn")
            return
        self.write('Terdengar: "%s"' % text, "ok")
        self.goal_var.set(text)
        if self.voice_cfg.get("auto_run", True):
            self.start()
        else:
            self.entry.focus_set()

    # ---------- memori ----------
    def teach(self):
        text = self._text("Ajari AI satu pelajaran singkat. Contoh:\n"
                          "Di Excel, menu Save As ada di File > Save a Copy.\n\n"
                          "Jangan tulis sandi atau data pribadi.")
        if not text:
            return
        app = self._text("Berlaku untuk aplikasi apa? (misal Excel; kosongkan untuk semua)") or ""
        if self.memory.add_lesson(text, app=app, source="user"):
            self.write("Pelajaran tersimpan.", "ok")
        else:
            self.write("Pelajaran tidak disimpan (sudah ada, terlalu pendek, atau mengandung data sensitif).", "warn")

    def add_fact(self):
        text = self._text("Tulis fakta dengan format  kunci=nilai\nContoh: folder_kerja=D:\\Kerja")
        if not text or "=" not in text:
            return
        key, value = text.split("=", 1)
        if self.memory.set_fact(key, value):
            self.write("Fakta tersimpan: %s" % key.strip(), "ok")
        else:
            self.write("Fakta tidak disimpan (kosong atau mengandung data sensitif).", "warn")

    def show_skills(self):
        win = ctk.CTkToplevel(self.root)
        win.title("Skill tersimpan")
        win.geometry("520x420")
        win.configure(fg_color=PALETTE["panel"])
        win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="Skill tersimpan", font=(FONT, 18, "bold")).pack(anchor="w", padx=18, pady=(16, 6))
        skills = self.memory.list_skills()
        chosen = {"i": None}
        box = ctk.CTkScrollableFrame(win, fg_color=PALETTE["card"], corner_radius=12)
        box.pack(fill="both", expand=True, padx=18, pady=6)
        btns = []

        def pick(i):
            chosen["i"] = i
            for k, b in enumerate(btns):
                b.configure(fg_color=PALETTE["accent"] if k == i else "transparent")

        if not skills:
            ctk.CTkLabel(box, text="Belum ada skill. Selesaikan tugas lalu simpan sebagai skill.",
                         text_color=PALETTE["muted"]).pack(padx=10, pady=20)
        for i, s in enumerate(skills):
            b = ctk.CTkButton(box, anchor="w", height=38, corner_radius=8, fg_color="transparent",
                              hover_color=PALETTE["line"], font=(FONT, 13),
                              text="%s   (%d langkah, berhasil %d/%d)" % (
                                  s["name"], len(s["steps"]), s.get("ok_runs", 0), s.get("runs", 0)),
                              command=lambda i=i: pick(i))
            b.pack(fill="x", pady=2)
            btns.append(b)

        def selected():
            return skills[chosen["i"]] if chosen["i"] is not None else None

        def run_it():
            s = selected()
            if s:
                self.goal_var.set(s["goal"])
                win.destroy()
                self.start()

        def delete_it():
            s = selected()
            if s and self._yesno('Hapus skill "%s"?' % s["name"]):
                self.memory.delete_skill(s["name"])
                win.destroy()

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(anchor="e", padx=18, pady=(6, 16))
        ctk.CTkButton(row, text="Hapus", width=90, height=34, corner_radius=10, fg_color=PALETTE["card"],
                      hover_color=PALETTE["bad"], command=delete_it).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Jalankan", width=110, height=34, corner_radius=10, fg_color=PALETTE["accent"],
                      hover_color=PALETTE["accent_hover"], command=run_it).pack(side="left")

    def test_model(self):
        ok, info = self.llm.ping()
        if ok:
            self.write("Server model terhubung. Model: " + info, "ok")
        else:
            self.write("Server model TIDAK terhubung: %s" % info, "bad")
        self._set_status(ok and "belum ada model" not in info,
                         "Model siap" if ok and "belum ada model" not in info else
                         "Model belum dimuat" if ok else "Model offline")

    # ---------- setup LM Studio ----------
    def _startup_checks(self):
        threading.Thread(target=self._startup_worker, daemon=True).start()

    def _startup_worker(self):
        bridge = GuiBridge(self.q, self.stop_event)
        ok, info = self.llm.ping()
        if ok and "belum ada model" not in info:
            if not self.llm.cfg.get("auto_switch_model", True):
                return
            try:
                wiz = Setup(bridge, self.llm, self.llm.cfg, self.config_path)
                if not wiz.needs_switch(info):
                    return
            except Exception:
                return
            bridge.log("Model yang termuat (%s) terlalu berat dan lambat untuk laptop ini. Beralih otomatis ke "
                       "model yang lebih cepat; jika gagal, model lama tetap dipakai." % info[:60])
            self._setup_worker(bridge)
            return
        bridge.log("Model AI belum siap. Menyiapkan otomatis (LM Studio + model), mohon tunggu...")
        self._setup_worker(bridge)

    def start_setup(self):
        if self.busy_setup or self.agent_running:
            return
        threading.Thread(target=self._setup_worker, args=(GuiBridge(self.q, self.stop_event),), daemon=True).start()

    def _setup_worker(self, bridge):
        if self.busy_setup:
            return
        self.busy_setup = True
        self.stop_event.clear()
        try:
            wiz = Setup(bridge, self.llm, self.llm.cfg, self.config_path)
            try:
                bridge.log(wiz.run_all())
            except SetupError as e:
                bridge.log("Setup berhenti: %s" % e)
            except Exception as e:
                bridge.log("Setup gagal tak terduga: %s" % e)
        finally:
            self.busy_setup = False

    def _on_close(self):
        self.stop_event.set()
        self.root.destroy()
