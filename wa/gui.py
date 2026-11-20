import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog

import os
import sys

from . import APP_TITLE, __version__
from .core import UI
from . import updater
from .lmstudio_setup import Setup, SetupError


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
        self.current_goal = ""

        root.title(APP_TITLE)
        root.geometry("760x520")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        top = tk.Frame(root)
        top.pack(fill="x", padx=8, pady=8)
        tk.Label(top, text="Tugas:").pack(side="left")
        self.goal_var = tk.StringVar()
        self.entry = tk.Entry(top, textvariable=self.goal_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda e: self.start())
        self.run_btn = tk.Button(top, text="Jalankan", command=self.start)
        self.run_btn.pack(side="left")
        self.stop_btn = tk.Button(top, text="Stop (F12)", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))

        self.log_box = scrolledtext.ScrolledText(root, height=20, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=8)

        bottom = tk.Frame(root)
        bottom.pack(fill="x", padx=8, pady=8)
        tk.Button(bottom, text="Ajari AI", command=self.teach).pack(side="left")
        tk.Button(bottom, text="Skill tersimpan", command=self.show_skills).pack(side="left", padx=6)
        tk.Button(bottom, text="Fakta tentang saya", command=self.add_fact).pack(side="left")
        tk.Button(bottom, text="Tes koneksi model", command=self.test_model).pack(side="left", padx=6)
        tk.Button(bottom, text="Setup otomatis", command=self.start_setup).pack(side="left")
        tk.Button(bottom, text="Cek update", command=lambda: self.check_update(manual=True)).pack(side="left", padx=6)

        self._setup_hotkey()
        self.write("Asisten PC versi %s. Tulis tugas lalu tekan Jalankan. Saat AI bekerja, jendela ini mengecil; "
                   "tekan F12 untuk berhenti." % __version__)
        self.root.after(100, self._poll)
        if auto_checks:
            self.root.after(1500, self._startup_checks)

    # ---------- util tampilan ----------
    def write(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        if self.overlay_label is not None:
            self.overlay_label.configure(text=msg[:70])

    def _setup_hotkey(self):
        key = self.cfg["agent"].get("stop_hotkey", "f12")
        try:
            import keyboard
            keyboard.add_hotkey(key, self.stop_event.set)
        except Exception as e:
            self.write("Catatan: tombol darurat %s tidak aktif (%s). Gunakan tombol Stop." % (key.upper(), e))

    def _make_overlay(self):
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        w, h = 380, 64
        x = ov.winfo_screenwidth() - w - 20
        y = ov.winfo_screenheight() - h - 70
        ov.geometry("%dx%d+%d+%d" % (w, h, x, y))
        frame = tk.Frame(ov, bg="#1f2937")
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="AI sedang bekerja. F12 = berhenti", fg="#fbbf24", bg="#1f2937").pack(anchor="w", padx=8, pady=(6, 0))
        self.overlay_label = tk.Label(frame, text="", fg="white", bg="#1f2937", anchor="w")
        self.overlay_label.pack(fill="x", padx=8)
        self.overlay = ov

    def _close_overlay(self):
        if self.overlay is not None:
            self.overlay.destroy()
        self.overlay = None
        self.overlay_label = None

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

    def _bring_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.update()

    def _release_front(self):
        self.root.attributes("-topmost", False)
        if self.agent_running:
            self.root.iconify()

    def _dialog_confirm(self, text, ev, holder):
        self._bring_front()
        try:
            self.root.bell()
        except Exception:
            pass
        holder[0] = messagebox.askyesno(APP_TITLE + ": konfirmasi", text, parent=self.root)
        self._release_front()
        ev.set()

    def _dialog_ask(self, text, ev, holder):
        self._bring_front()
        holder[0] = simpledialog.askstring(APP_TITLE + ": AI bertanya", text, parent=self.root)
        self._release_front()
        ev.set()

    def _finished(self, result):
        self.agent_running = False
        self._close_overlay()
        self.root.deiconify()
        self.root.lift()
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        labels = {"done": "SELESAI", "failed": "GAGAL", "stopped": "DIHENTIKAN", "max_steps": "BATAS LANGKAH"}
        self.write("== %s: %s" % (labels.get(result.status, result.status), result.summary))
        if result.status == "done" and result.executed:
            name = simpledialog.askstring(
                APP_TITLE,
                "Tugas selesai. Simpan langkah-langkahnya sebagai skill?\n"
                "Skill bisa diputar ulang tanpa AI (lebih cepat).\n\n"
                "Isi nama skill, atau kosongkan untuk tidak menyimpan:",
                initialvalue=self.current_goal[:40], parent=self.root)
            if name and self.memory.save_skill(name, self.current_goal, result.executed):
                self.write('Skill "%s" tersimpan (%d langkah).' % (name, len(result.executed)))

    # ---------- memori ----------
    def teach(self):
        text = simpledialog.askstring(
            APP_TITLE,
            "Ajari AI satu pelajaran singkat. Contoh:\n"
            "Di Excel, menu Save As ada di File > Save a Copy.\n\n"
            "Jangan tulis sandi atau data pribadi.", parent=self.root)
        if not text:
            return
        app = simpledialog.askstring(APP_TITLE, "Berlaku untuk aplikasi apa? (misal Excel; kosongkan untuk semua)",
                                     parent=self.root) or ""
        if self.memory.add_lesson(text, app=app, source="user"):
            self.write("Pelajaran tersimpan.")
        else:
            self.write("Pelajaran tidak disimpan (sudah ada, terlalu pendek, atau mengandung data sensitif).")

    def add_fact(self):
        text = simpledialog.askstring(
            APP_TITLE, "Tulis fakta dengan format  kunci=nilai\nContoh: folder_kerja=D:\\Kerja", parent=self.root)
        if not text or "=" not in text:
            return
        key, value = text.split("=", 1)
        if self.memory.set_fact(key, value):
            self.write("Fakta tersimpan: %s" % key.strip())
        else:
            self.write("Fakta tidak disimpan (kosong atau mengandung data sensitif).")

    def show_skills(self):
        win = tk.Toplevel(self.root)
        win.title("Skill tersimpan")
        lb = tk.Listbox(win, width=60, height=12)
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        skills = self.memory.list_skills()
        for s in skills:
            lb.insert("end", "%s  (%d langkah, berhasil %d/%d)" % (s["name"], len(s["steps"]), s.get("ok_runs", 0), s.get("runs", 0)))

        def selected():
            sel = lb.curselection()
            return skills[sel[0]] if sel else None

        def run_it():
            s = selected()
            if s:
                self.goal_var.set(s["goal"])
                win.destroy()
                self.start()

        def delete_it():
            s = selected()
            if s and messagebox.askyesno(APP_TITLE, 'Hapus skill "%s"?' % s["name"], parent=win):
                self.memory.delete_skill(s["name"])
                win.destroy()

        row = tk.Frame(win)
        row.pack(pady=(0, 8))
        tk.Button(row, text="Jalankan", command=run_it).pack(side="left", padx=4)
        tk.Button(row, text="Hapus", command=delete_it).pack(side="left", padx=4)

    def test_model(self):
        ok, info = self.llm.ping()
        if ok:
            self.write("Server model terhubung. Model: " + info)
        else:
            self.write("Server model TIDAK terhubung: %s" % info)

    # ---------- setup LM Studio ----------
    def _startup_checks(self):
        threading.Thread(target=self._startup_worker, daemon=True).start()

    def _startup_worker(self):
        bridge = GuiBridge(self.q, self.stop_event)
        self._check_update_worker(bridge, manual=False)
        ok, _info = self.llm.ping()
        if not ok:
            bridge.log("Server model belum menyala.")
            if bridge.confirm("Server model (LM Studio) belum menyala.\n\nJalankan Setup otomatis sekarang? "
                              "Aplikasi akan memasang/menyalakan LM Studio dan menyiapkan model."):
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

    # ---------- update ----------
    def check_update(self, manual=False):
        threading.Thread(target=self._check_update_worker,
                         args=(GuiBridge(self.q, self.stop_event), manual), daemon=True).start()

    def _check_update_worker(self, bridge, manual):
        info = updater.check_for_update(__version__)
        if not info:
            if manual:
                bridge.log("Tidak ada update. Versi %s sudah yang terbaru (atau internet tidak terjangkau)." % __version__)
            return
        bridge.log("Update tersedia: versi %s (sekarang %s)." % (info["version"], __version__))
        notes = (info.get("notes") or "")[:400]
        if not bridge.confirm("Update Asisten PC versi %s tersedia.\n\n%s\n\nPasang sekarang? Aplikasi akan "
                              "menutup sendiri lalu terbuka lagi. Pengaturan, skill, dan memori Anda tetap aman."
                              % (info["version"], notes)):
            return
        try:
            bridge.log("Mengunduh update...")
            last = [0]

            def prog(got, total):
                pct = int(got * 100 / total)
                if pct >= last[0] + 20:
                    last[0] = pct
                    bridge.log("Unduh %d%%" % pct)

            updater.prepare_and_swap(info, self.base_dir, os.path.join(self.base_dir, "data", "update"), prog)
        except Exception as e:
            bridge.log("Update gagal: %s" % e)
            if info.get("page"):
                bridge.log("Unduh manual di: %s" % info["page"])
            return
        bridge.log("Update siap. Menutup aplikasi untuk memasang...")
        self.q.put(("call", self._on_close))

    def _on_close(self):
        self.stop_event.set()
        self.root.destroy()
