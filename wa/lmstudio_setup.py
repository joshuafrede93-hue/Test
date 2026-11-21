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
    # (RAM minimum GB, label, perkiraan ukuran GB, kunci `lms get` cadangan, repo Hugging Face, nama berkas)
    (15.0, "Qwen3 14B (Q4_K_M)", 9.0, ["lmstudio-community/Qwen3-14B-GGUF"],
     "Qwen/Qwen3-14B-GGUF", "Qwen3-14B-Q4_K_M.gguf"),
    (11.0, "Qwen3 8B (Q4_K_M)", 5.5, ["lmstudio-community/Qwen3-8B-GGUF"],
     "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf"),
    (7.0, "Qwen3 4B (Q4_K_M)", 2.8, ["lmstudio-community/Qwen3-4B-GGUF"],
     "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf"),
    (0.0, "Qwen2.5 3B Instruct (Q4_K_M)", 2.0, ["lmstudio-community/Qwen2.5-3B-Instruct-GGUF"],
     "Qwen/Qwen2.5-3B-Instruct-GGUF", "qwen2.5-3b-instruct-q4_k_m.gguf"),
]

# Cadangan bila winget tidak ada: installer resmi LM Studio (pola URL dari katalog paket resmi).
FALLBACK_INSTALLER = "https://installers.lmstudio.ai/win32/x64/0.4.28-2/LM-Studio-0.4.28-2-x64.exe"
MIN_MODEL_BYTES = 500 * 1024 * 1024  # entri katalog kecil (KB) bukan model sungguhan

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\[[su]")
_SPIN = re.compile(r"^[\u2800-\u28ff\s]*$")


def clean_line(raw):
    """Buang kode warna/kursor dan spinner dari keluaran lms."""
    text = _ANSI.sub("", raw).replace("\x1b", "").strip()
    text = re.sub(r"^[\u2800-\u28ff]\s*", "", text)
    return "" if _SPIN.match(text) else text


class SetupError(Exception):
    pass


def choose_model(ram_gb):
    tier = next((t for t in MODEL_TIERS if ram_gb >= t[0]), MODEL_TIERS[-1])
    _min, label, size, keys, repo, fname = tier
    return {"label": label, "size_gb": size, "keys": list(keys), "repo": repo, "file": fname,
            "url": "https://huggingface.co/%s/resolve/main/%s" % (repo, fname)}


def models_dir():
    return os.path.join(os.path.expanduser("~"), ".lmstudio", "models")


def download_file(url, dest, on_progress=None, should_stop=lambda: False, opener=None, timeout=60):
    """Unduh dengan dukungan lanjut (resume). Mengembalikan ukuran akhir; melempar SetupError bila gagal."""
    import urllib.error
    import urllib.request
    op = opener or urllib.request.build_opener()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    for attempt in range(8):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": "AsistenPC"}
        if have:
            headers["Range"] = "bytes=%d-" % have
        try:
            with op.open(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                code = getattr(r, "status", 200)
                if have and code != 206:
                    have = 0  # server mengabaikan Range: mulai dari awal
                length = int(r.headers.get("Content-Length", 0) or 0)
                total = have + length if length else 0
                mode = "ab" if have else "wb"
                with open(part, mode) as f:
                    got = have
                    while True:
                        if should_stop():
                            raise SetupError("Setup dihentikan.")
                        chunk = r.read(1024 * 512)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if on_progress:
                            on_progress(got, total)
            if total and os.path.getsize(part) != total:
                raise OSError("unduhan terpotong (%d dari %d)" % (os.path.getsize(part), total))
            os.replace(part, dest)
            return os.path.getsize(dest)
        except SetupError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416 and os.path.exists(part):  # sudah lengkap
                os.replace(part, dest)
                return os.path.getsize(dest)
            if e.code in (401, 403, 404):
                raise SetupError("Server unduhan menolak (%s) untuk %s" % (e.code, url))
            time.sleep(min(30, 3 * (attempt + 1)))
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(min(30, 3 * (attempt + 1)))
    raise SetupError("Unduhan gagal setelah beberapa kali mencoba (internet putus?). Buka ulang aplikasi untuk melanjutkan.")


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
    last = {"t": 0.0, "text": ""}

    def emit(text):
        text = clean_line(text)
        if not text:
            return
        lines.append(text)
        now = time.time()
        if on_line and text != last["text"] and now - last["t"] >= 2.0:
            last["t"], last["text"] = now, text
            on_line(text[:160])

    def reader():
        buf = b""
        while True:
            chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]+", buf)
            buf = parts.pop()
            for part in parts:
                emit(part.decode("utf-8", "replace"))
        if buf:
            emit(buf.decode("utf-8", "replace"))

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
            size = m.get("sizeBytes")
            if isinstance(size, (int, float)) and size < MIN_MODEL_BYTES:
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
                 sleep=time.sleep, finder_lms=find_lms, finder_exe=find_lmstudio_exe, launcher=None,
                 downloader=None, models_path=None, free_gb=None, auto=True):
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
        self.downloader = downloader or download_file
        self.models_path = models_path or models_dir()
        self.free_gb = free_gb
        self.auto = auto  # True = tidak bertanya lagi (pengguna sudah memberi izin lewat pengaturan awal)

    def _ok_to(self, question):
        return True if self.auto else self.ui.confirm(question)

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
    def _install_lmstudio(self):
        self.log("LM Studio belum terpasang. Memasang otomatis (beberapa menit, jangan tutup aplikasi)...")
        code, out = self.run(
            ["winget", "install", "-e", "--id", WINGET_ID, "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
            timeout=1800, on_line=self.log)
        if self.find_exe():
            return
        self.log("winget tidak berhasil (kode %s). Mencoba installer resmi langsung..." % code)
        tmp = os.path.join(os.environ.get("TEMP", self.models_path), "LM-Studio-Setup.exe")
        try:
            self._download(FALLBACK_INSTALLER, tmp, "installer LM Studio")
            code, out = self.run([tmp, "/S", "/currentuser"], timeout=1800)
        except SetupError as e:
            out = str(e)
        if not self.find_exe():
            raise SetupError("Pemasangan LM Studio otomatis gagal. Pasang manual dari https://lmstudio.ai "
                             "lalu buka ulang Asisten PC.\n%s" % out[-300:])

    def _download(self, url, dest, label):
        last = {"pct": -10}

        def prog(got, total):
            if total:
                pct = int(got * 100 / total)
                if pct >= last["pct"] + 5:
                    last["pct"] = pct
                    self.log("Unduh %s: %d%% (%.1f / %.1f GB)" % (label, pct, got / 1024 ** 3, total / 1024 ** 3))

        return self.downloader(url, dest, prog, self.ui.should_stop)

    def ensure_lms(self):
        exe = self.find_exe()
        if not exe:
            self._install_lmstudio()
            exe = self.find_exe()
            self.log("LM Studio terpasang.")
            fresh = True
        else:
            fresh = False
        lms = self.find_lms()
        if lms and not fresh:
            return lms
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
                             "tunggu sampai tampil penuh, lalu buka ulang Asisten PC.")
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
        wanted = [plan["file"].rsplit(".", 1)[0]] + plan["keys"]
        found = self.pick_existing(self.downloaded_models(lms), wanted)
        if found:
            self.log("Model sudah ada di komputer: %s" % found)
            return found
        # 1) unduh langsung (bisa dilanjutkan, progres jelas)
        dest = os.path.join(self.models_path, *plan["repo"].split("/"), plan["file"])
        free = self.free_gb
        if free is None:
            try:
                os.makedirs(self.models_path, exist_ok=True)
                free = shutil.disk_usage(self.models_path).free / 1024 ** 3
            except OSError:
                free = 999
        if free < plan["size_gb"] + 2:
            raise SetupError("Ruang disk kurang: tersisa %.1f GB, butuh sekitar %.1f GB." % (free, plan["size_gb"] + 2))
        self.log("Mengunduh model %s (%.1f GB). Ini bisa 15-60 menit; jangan tutup aplikasi." % (plan["label"], plan["size_gb"]))
        try:
            self._download(plan["url"], dest, "model")
        except SetupError as e:
            if "dihentikan" in str(e):
                raise
            self.log("Unduh langsung gagal (%s). Mencoba lewat LM Studio..." % e)
        found = self.pick_existing(self.downloaded_models(lms), wanted)
        if found:
            self.log("Model siap: %s" % found)
            return found
        # 2) cadangan: lms get
        last = ""
        for key in plan["keys"]:
            self._check_stop()
            self.log("Mengunduh %s lewat lms..." % key)
            code, out = self._lms(lms, ["get", key, "--yes"], timeout=7200, stdin_text="y\ny\ny\n", stream=True)
            if code != 0 and re.search(r"unknown option|unrecognized", out, re.I):
                code, out = self._lms(lms, ["get", key], timeout=7200, stdin_text="y\ny\ny\n", stream=True)
            last = out[-200:]
            found = self.pick_existing(self.downloaded_models(lms), wanted)
            if found:
                return found
        raise SetupError("Model tidak berhasil diunduh.\n%s" % last)

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
