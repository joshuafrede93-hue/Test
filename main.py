import os
import sys

# Harus dilakukan sebelum library GUI/otomasi dimuat: samakan koordinat layar dengan piksel asli
# (laptop dengan skala tampilan 125% akan meleset kalau ini tidak diatur).
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import argparse

from wa import APP_TITLE
from wa.config import load_config, save_default_config
from wa.core import Agent, UI
from wa.llm import LocalLLM
from wa.memory import Memory
from wa.safety import Safety

# Saat dibungkus jadi .exe, config.json dan folder data/ harus berada di samping file .exe.
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))

# Mode tanpa konsol (windowed): stdout/stderr kosong, arahkan ke file log agar error tetap bisa dibaca.
if sys.stdout is None or sys.stderr is None:
    try:
        os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
        _log = open(os.path.join(BASE, "data", "log.txt"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or _log
        sys.stderr = sys.stderr or _log
    except OSError:
        pass


def _show_error(text):
    print(text)
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_TITLE, text)
        r.destroy()
    except Exception:
        pass


class ConsoleUI(UI):
    def log(self, msg):
        print(msg, flush=True)

    def confirm(self, msg):
        print("\n" + msg)
        return input("Izinkan? [y/N] ").strip().lower() in ("y", "ya", "yes")

    def ask(self, question):
        return input("AI bertanya: %s\n> " % question)


def main():
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--cli", metavar="TUGAS", help="jalankan satu tugas lewat konsol tanpa GUI")
    parser.add_argument("--config", default=os.path.join(BASE, "config.json"))
    args = parser.parse_args()

    if sys.platform != "win32":
        print("Aplikasi ini hanya bisa dijalankan di Windows.")
        return 1

    save_default_config(args.config)
    cfg = load_config(args.config)
    memory = Memory(os.path.join(BASE, "data"))
    llm = LocalLLM(cfg["llm"])
    safety = Safety(cfg["safety"])

    try:
        from wa.env_windows import WindowsEnv
        env = WindowsEnv(cfg["llm"], cfg["agent"], own_titles={APP_TITLE})
    except ImportError as e:
        _show_error("Library belum terpasang (%s). Jalankan install.bat dulu." % e)
        return 1

    def make_agent(ui):
        return Agent(llm, env, memory, safety, ui, cfg)

    if args.cli:
        ui = ConsoleUI()
        env.thread_init()
        result = make_agent(ui).run(args.cli)
        print("\n%s: %s" % (result.status.upper(), result.summary))
        return 0 if result.status == "done" else 2

    import customtkinter as ctk
    from wa.gui import App
    root = ctk.CTk()
    App(root, cfg, memory, llm, make_agent, config_path=args.config, base_dir=BASE)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
