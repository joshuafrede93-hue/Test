"""Setup otomatis LM Studio: pasang, unduh model, muat model, nyalakan server, tes, simpan config.

Semua perintah memakai `lms`, CLI resmi yang ikut terpasang bersama LM Studio.
Fungsi yang menyentuh sistem (menjalankan perintah, membaca RAM) dibuat bisa diganti
supaya logikanya bisa diuji tanpa Windows.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

WINGET_ID = "ElementLabs.LMStudio"
DEFAULT_PORT = 1234

# Kandidat model, dari yang paling akurat ke yang paling ringan. Tiap model punya beberapa
# nama kunci karena nama di katalog LM Studio bisa berubah; wizard mencoba berurutan.
MODEL_TIERS = [
    # (RAM minimum GB, label, perkiraan ukuran GB, daftar kunci unduhan)
    (15.0, "Qwen3 14B (Q4_K_M)", 9.0,
     ["qwen/qwen3-14b", "lmstudio-community/Qwen3-14B-GGUF"]),
    (11.0, "Qwen3 8B (Q4_K_M)", 5.5,
     ["qwen/qwen3-8b", "lmstudio-community/Qwen3-8B-GGUF"]),
    (7.0, "Qwen3 4B (Q4_K_M)", 2.8,
     ["qwen/qwen3-4b", "lmstudio-community/Qwen3-4B-GGUF"]),
    (0.0, "Qwen2.5 3B Instruct (Q4)", 2.0,
     ["qwen2.5-3b-instruct", "lmstudio-community/Qwen2.5-3B-Instruct-GGUF"]),
]


class SetupError(Exception):
    pass


def choose_model(ram_gb):
    for min_ram, label, size, keys in MODEL_TIERS:
        if ram_gb >= min_ram:
            return {"label": label, "size_gb": size, "keys": list(keys)}
    return {"label": MODEL_TIERS[-1][1], "size_gb": MODEL_TIERS[-1][2], "keys": list(MODEL_TIERS[-1][3])}


def system_ram_gb():
    """Total RAM dalam GB (Windows). Mengembalikan 0.0 jika gagal."""
    if sys.platform != "win32":
        return 0.0
    try:
        import ctypes

        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMSTAT()
        st.dwLength = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys / (1024 ** 3)
    except Exception:
        return 0.0


def default_runner(args, timeout=120, stdin_text=None, on_line=None):
    """Jalankan perintah tanpa jendela konsol. Mengembalikan (kode_keluar, teks_keluaran)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, creationflags=flags)
    except OSError as e:
        return 127, str(e)
    if stdin_text is not None:
        try:
            p.stdin.write(stdin_text.encode("utf-8"))
        except OSError:
            pass
    try:
        p.stdin.close()
    except OSError:
        pass
    lines = []
    start = time.time()
    import threading

    def reader():
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            lines.append(line)
            if on_line and line.strip():
                on_line(line)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.5)
        if time.time() - start > timeout:
            try:
                p.kill()
            except OSError:
                pass
            return 124, "\n".join(lines) + "\n(waktu habis)"
    p.wait()
    return p.returncode, "\n".join(lines)


def find_lms():
    path = shutil.which("lms")
    if path:
        return path
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".lmstudio", "bin", "lms.exe"),
                 os.path.join(home, ".cache", "lm-studio", "bin", "lms.exe")):
        if os.path.exists(cand):
            return cand
    return None


def find_lmstudio_exe():
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for cand in (os.path.join(local, "Programs", "LM Studio", "LM Studio.exe"),
                 os.path.join(local, "LM-Studio", "LM Studio.exe"),
                 os.path.join(pf, "LM Studio", "LM Studio.exe")):
        if os.path.exists(cand):
            return cand
    return None


def parse_models(ls_output):
    """Ambil daftar kunci model LLM dari keluaran `lms ls --json` (atau teks biasa)."""
    keys = []
    try:
        data = json.loads(ls_output)
        if isinstance(data, dict):
            data = data.get("models") or data.get("data") or []
        for m in data:
            if not isinstance(m, dict):
                continue
            kind = str(m.get("type", "llm")).lower()
            if kind not in ("llm", "vlm"):
                continue
            key = m.get("modelKey") or m.get("path") or m.get("id")
            if key:
                keys.append(str(key))
        return keys
    except (json.JSONDecodeError, TypeError):
        pass
    for line in ls_output.splitlines():
        line = line.strip()
        if "/" in line and not line.lower().startswith(("you have", "llm", "embedding")):
            keys.append(line.split()[0])
    return keys


def parse_port(status_text):
    m = re.search(r"(?:port|localhost:|127\.0\.0\.1:)\s*:?\s*(\d{2,5})", status_text, re.I)
    return int(m.group(1)) if m else None


def update_config_file(path, base_url=None, model=None, reasoning=False):
    """Perbarui config.json tanpa menghapus pengaturan lain."""
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    llm = data.setdefault("llm", {})
    if base_url:
        llm["base_url"] = base_url
    if model is not None:
        llm["model"] = model
    if reasoning:
        llm["max_tokens"] = max(int(llm.get("max_tokens", 400)), 1500)
        llm["timeout"] = max(int(llm.get("timeout", 240)), 600)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


class Setup:
    """Wizard satu klik. `ui` cukup punya log(msg), confirm(msg), should_stop()."""

    def __init__(self, ui, llm, cfg, config_path, runner=default_runner, ram_gb=None,
                 sleep=time.sleep, finder_lms=find_lms, finder_exe=find_lmstudio_exe, launcher=None):
        self.ui = ui
        self.llm = llm
        self.cfg = cfg  # dict cfg["llm"] yang dipakai LocalLLM (diubah langsung)
        self.config_path = config_path
        self.run = runner
        self.ram_gb = system_ram_gb() if ram_gb is None else ram_gb
        self.sleep = sleep
        self.find_lms = finder_lms
        self.find_exe = finder_exe
        self.launcher = launcher or self._launch

    # ---------- util ----------
    def log(self, msg):
        self.ui.log(msg)

    def _check_stop(self):
        if self.ui.should_stop():
            raise SetupError("Setup dihentikan.")

    def _launch(self, exe):
        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([exe], creationflags=flags, close_fds=True)

    def _lms(self, lms, args, timeout=120, stdin_text=None, stream=False):
        return self.run([lms] + args, timeout=timeout, stdin_text=stdin_text,
                        on_line=(self.log if stream else None))

    def _wait_for(self, pred, seconds, step=2.0):
        waited = 0.0
        while waited < seconds:
            self._check_stop()
            val = pred()
            if val:
                return val
            self.sleep(step)
            waited += step
        return pred()

    # ---------- langkah ----------
    def ensure_lms(self):
        lms = self.find_lms()
        if lms:
            return lms
        exe = self.find_exe()
        if not exe:
            self.log("LM Studio belum terpasang.")
            if not self.ui.confirm(
                    "LM Studio belum terpasang.\n\nPasang otomatis lewat winget (resmi, gratis)? "
                    "Unduhan sekitar 1 GB lebih dan butuh beberapa menit."):
                raise SetupError("Pemasangan LM Studio dibatalkan.")
            self.log("Memasang LM Studio (mohon tunggu, jangan tutup jendela ini)...")
            code, out = self.run(
                ["winget", "install", "-e", "--id", WINGET_ID, "--silent",
                 "--accept-package-agreements", "--accept-source-agreements"],
                timeout=1800, on_line=self.log)
            exe = self.find_exe()
            if code != 0 and not exe:
                raise SetupError(
                    "Pemasangan otomatis gagal (kode %s). Pasang manual dari https://lmstudio.ai lalu tekan "
                    "Setup otomatis lagi.\n%s" % (code, out[-300:]))
            self.log("LM Studio terpasang.")
        # lms baru tersedia setelah LM Studio dibuka sekali.
        self.log("Membuka LM Studio sekali agar perangkat lms siap (bisa sampai 1-2 menit)...")
        if exe:
            try:
                self.launcher(exe)
            except Exception as e:
                self.log("Gagal membuka LM Studio otomatis: %s" % e)
        lms = self._wait_for(self.find_lms, 150, step=3.0)
        if not lms:
            raise SetupError("Perangkat lms belum muncul. Buka LM Studio sekali secara manual, "
                             "tunggu sampai tampil penuh, lalu tekan Setup otomatis lagi.")
        return lms

    def downloaded_models(self, lms):
        code, out = self._lms(lms, ["ls", "--json"], timeout=60)
        if code != 0:
            code, out = self._lms(lms, ["ls"], timeout=60)
        return parse_models(out) if code == 0 else []

    def pick_existing(self, models, wanted_keys):
        """Cari model yang sudah diunduh dan cocok dengan salah satu kunci kandidat."""
        for key in wanted_keys:
            tail = key.split("/")[-1].lower().replace("-gguf", "")
            for m in models:
                if tail in m.lower():
                    return m
        return None

    def ensure_model(self, lms):
        plan = choose_model(self.ram_gb or 8.0)
        self.log("RAM terdeteksi %.1f GB. Model yang dipilih: %s (sekitar %.1f GB)."
                 % (self.ram_gb, plan["label"], plan["size_gb"]))
        have = self.downloaded_models(lms)
        found = self.pick_existing(have, plan["keys"])
        if found:
            self.log("Model sudah ada di komputer: %s" % found)
            return found
        if not self.ui.confirm(
                "Akan mengunduh model %s (sekitar %.1f GB, gratis).\n\nPastikan ruang disk cukup dan "
                "internet stabil. Lanjutkan?" % (plan["label"], plan["size_gb"])):
            raise SetupError("Unduhan model dibatalkan.")
        last = ""
        for key in plan["keys"]:
            self._check_stop()
            self.log("Mengunduh %s ..." % key)
            code, out = self._lms(lms, ["get", key, "--yes"], timeout=7200, stdin_text="y\ny\ny\n", stream=True)
            if code != 0 and re.search(r"unknown option|unrecognized", out, re.I):
                code, out = self._lms(lms, ["get", key], timeout=7200, stdin_text="y\ny\ny\n", stream=True)
            last = out[-200:]
            have = self.downloaded_models(lms)
            found = self.pick_existing(have, [key]) or self.pick_existing(have, plan["keys"])
            if found:
                self.log("Unduhan selesai: %s" % found)
                return found
            self.log("Kunci %s tidak berhasil, mencoba nama lain..." % key)
        raise SetupError("Model tidak berhasil diunduh. Unduh manual lewat LM Studio (tab Discover), "
                         "lalu tekan Setup otomatis lagi.\n%s" % last)

    def load_model(self, lms, model_key):
        self.log("Memuat model ke memori (konteks 8192)...")
        attempts = [["load", model_key, "--context-length", "8192", "--yes"],
                    ["load", model_key, "--yes"],
                    ["load", model_key]]
        out = ""
        for args in attempts:
            self._check_stop()
            code, out = self._lms(lms, args, timeout=900, stream=True)
            if code == 0:
                return True
        raise SetupError("Model gagal dimuat.\n%s" % out[-300:])

    def start_server(self, lms):
        self.log("Menyalakan server di port %d..." % DEFAULT_PORT)
        code, out = self._lms(lms, ["server", "start", "--port", str(DEFAULT_PORT)], timeout=120)
        if code != 0:
            code, out = self._lms(lms, ["server", "start"], timeout=120)
        _, status = self._lms(lms, ["server", "status"], timeout=30)
        port = parse_port(status) or parse_port(out) or DEFAULT_PORT
        base = "http://localhost:%d/v1" % port
        if base != self.cfg.get("base_url"):
            self.log("Alamat server disesuaikan menjadi %s" % base)
        self.cfg["base_url"] = base
        return base

    def verify(self):
        def ok():
            good, info = self.llm.ping()
            return info if good and "belum ada model" not in info else None
        info = self._wait_for(ok, 45, step=3.0)
        if not info:
            raise SetupError("Server menyala tetapi belum ada model termuat atau belum menjawab.")
        return info

    def run_all(self):
        """Jalankan semuanya. Mengembalikan teks ringkasan; melempar SetupError bila gagal."""
        self.log("== Setup otomatis LM Studio dimulai")
        lms = self.ensure_lms()
        model_key = self.ensure_model(lms)
        self._check_stop()
        self.load_model(lms, model_key)
        base = self.start_server(lms)
        info = self.verify()
        self.cfg["max_tokens"] = max(int(self.cfg.get("max_tokens", 400)), 1500)
        self.cfg["timeout"] = max(int(self.cfg.get("timeout", 240)), 600)
        update_config_file(self.config_path, base_url=base, model="", reasoning=True)
        self.log("Tes jawaban model...")
        try:
            reply = self.llm.chat([{"role": "user", "content": "Jawab satu kata: siap?"}])
            self.log("Model menjawab: %s" % (reply[:60] or "(kosong)"))
        except Exception as e:
            raise SetupError("Server terhubung tetapi model gagal menjawab: %s" % e)
        return "Setup selesai. Model termuat: %s. Alamat: %s" % (info, base)
