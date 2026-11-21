"""Uji asap GUI: buka jendela, tulis log, buka dialog skill, lalu tutup. Dijalankan di Windows (CI)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import customtkinter as ctk

from wa.config import load_config
from wa.gui import App
from wa.memory import Memory


class FakeLLM:
    def __init__(self, cfg):
        self.cfg = cfg

    def ping(self):
        return False, "offline"


cfg = load_config(None)
tmp = tempfile.mkdtemp()
root = ctk.CTk()
app = App(root, cfg, Memory(tmp), FakeLLM(cfg["llm"]), lambda ui: None,
          config_path=os.path.join(tmp, "config.json"), base_dir=tmp, auto_checks=False)
app.write("== Tugas: uji")
app.write("== SELESAI: ok")
app.show_skills()
from wa.gui import Dialog
d1 = Dialog(root, "t", "Izinkan?\naksi", "yesno", header="Butuh persetujuan Anda")
d2 = Dialog(root, "t", "Pertanyaan", "text", header="AI butuh jawaban Anda")
app._make_overlay()
app._alert("AI butuh persetujuan Anda", "klik Simpan")
app._after_dialog()
root.update()
root.after(700, root.update)
for _ in range(10):
    root.update()
    root.after(80)
d1.destroy()
d2.destroy()
app._close_overlay()
assert app.mic_btn.cget("state") == "normal" and app.run_btn.cget("text") == ""
root.update()
root.after(500, root.destroy)
root.mainloop()
print("GUI smoke OK")
