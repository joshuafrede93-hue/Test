"""Pengenalan suara offline (Whisper lewat faster-whisper). Model diunduh sekali dari Hugging Face (base = 145 MB)
ke folder data, lalu berjalan tanpa internet dan tanpa mengirim suara ke mana pun."""
import os
import threading

REPO = "Systran/faster-whisper-%s"
FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
SIZES_MB = {"tiny": 75, "base": 145, "small": 465}


class OfflineError(Exception):
    pass


def model_dir(base_dir, size):
    return os.path.join(base_dir, "whisper-" + size)


def is_ready(base_dir, size):
    d = model_dir(base_dir, size)
    return all(os.path.exists(os.path.join(d, f)) for f in FILES)


def ensure_model(base_dir, size="base", log=lambda m: None, downloader=None, should_stop=lambda: False):
    """Pastikan berkas model ada; unduh yang kurang (bisa dilanjutkan). Mengembalikan folder model."""
    d = model_dir(base_dir, size)
    if is_ready(base_dir, size):
        return d
    if downloader is None:
        from .lmstudio_setup import download_file as downloader
    log("Mengunduh model suara offline (%s, sekitar %d MB)..." % (size, SIZES_MB.get(size, 0)))
    last = [-1]
    for name in FILES:
        dest = os.path.join(d, name)
        if os.path.exists(dest):
            continue
        url = "https://huggingface.co/%s/resolve/main/%s" % (REPO % size, name)

        def progress(got, total, name=name):
            if total and name == "model.bin":
                pct = int(got * 100 / total) // 10 * 10
                if pct != last[0]:
                    last[0] = pct
                    log("Unduh model suara: %d%%" % pct)
        try:
            downloader(url, dest, progress, should_stop)
        except Exception as e:
            raise OfflineError("Gagal mengunduh %s: %s" % (name, e))
    log("Model suara offline siap.")
    return d


class OfflineRecognizer:
    def __init__(self, base_dir, size="base", log=lambda m: None):
        self.base_dir, self.size, self.log = base_dir, size, log
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            path = ensure_model(self.base_dir, self.size, self.log)
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=4)
            except Exception as e:
                raise OfflineError("Model suara offline gagal dimuat: %s" % e)
        return self._model

    def transcribe(self, pcm, language="id-ID"):
        """pcm: bytes int16 mono 16 kHz. Mengembalikan teks ("" bila tidak ada ucapan)."""
        import numpy as np
        with self._lock:
            model = self._load()
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 0:
                audio = audio * min(8.0, 0.5 / peak)  # naikkan volume rekaman yang pelan
            segments, _info = model.transcribe(
                audio, language=(language or "id")[:2].lower(), beam_size=3, vad_filter=False,
                condition_on_previous_text=False, temperature=0.0)
            parts = [seg.text.strip() for seg in segments if getattr(seg, "no_speech_prob", 0.0) < 0.6]
        return " ".join(p for p in parts if p).strip()
