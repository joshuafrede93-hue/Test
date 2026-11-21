"""Perintah suara: rekam dari mikrofon sampai pengguna selesai bicara, lalu ubah jadi teks.

Perekaman memakai sounddevice (tanpa PyAudio). Pengenalan suara memakai layanan gratis Google lewat
paket SpeechRecognition, jadi butuh internet. Hanya potongan suara yang dikirim, tanpa akun atau kunci.
"""
import array
import math
import time

RATE = 16000
CHUNK = 1600  # 0,1 detik per potongan


class VoiceError(Exception):
    pass


def rms(raw):
    """Kekuatan suara rata-rata satu potongan int16 mono."""
    n = len(raw) // 2
    if n == 0:
        return 0.0
    a = array.array("h")
    a.frombytes(raw[: n * 2])
    return math.sqrt(sum(x * x for x in a) / n)


def record_until_silence(read_chunk, should_stop=lambda: False, max_seconds=15.0, silence_seconds=1.2,
                         start_timeout=6.0, on_level=None, chunk_seconds=CHUNK / RATE):
    """Baca potongan suara sampai pengguna berhenti bicara.

    read_chunk() -> bytes int16 mono sepanjang 0,1 detik. Mengembalikan bytes audio, atau b"" bila
    tidak ada yang bicara. Ambang suara menyesuaikan dengan bising ruangan.
    """
    ambient = []
    preroll = []
    audio = []
    heard = False
    quiet = 0.0
    elapsed = 0.0
    threshold = 600.0
    while elapsed < max_seconds:
        if should_stop():
            return b""
        chunk = read_chunk()
        elapsed += chunk_seconds
        level = rms(chunk)
        if on_level:
            on_level(level)
        if not heard:
            if len(ambient) < 3:  # 0,3 detik pertama dipakai mengukur bising ruangan
                ambient.append(level)
                threshold = max(500.0, 3.0 * (sum(ambient) / len(ambient)))
                preroll.append(chunk)
                continue
            if level > threshold:
                heard = True
                audio.extend(preroll[-4:])
                audio.append(chunk)
                quiet = 0.0
            else:
                preroll.append(chunk)
                preroll[:] = preroll[-4:]
                if elapsed >= start_timeout:
                    return b""
        else:
            audio.append(chunk)
            if level > threshold * 0.7:
                quiet = 0.0
            else:
                quiet += chunk_seconds
                if quiet >= silence_seconds:
                    break
    return b"".join(audio) if heard else b""


def transcribe(pcm, language="id-ID"):
    """Ubah audio mentah (16 kHz, int16, mono) menjadi teks. Mengembalikan "" bila tidak dikenali."""
    try:
        import speech_recognition as sr
    except ImportError:
        raise VoiceError("Paket SpeechRecognition belum terpasang (jalankan install.bat).")
    rec = sr.Recognizer()
    audio = sr.AudioData(pcm, RATE, 2)
    try:
        return rec.recognize_google(audio, language=language).strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        raise VoiceError("Layanan pengenalan suara tidak terjangkau (%s). Periksa koneksi internet." % e)


def open_microphone():
    """Buka mikrofon bawaan. Mengembalikan (read_chunk, close)."""
    try:
        import sounddevice as sd
    except Exception as e:  # ImportError atau PortAudio tidak ada
        raise VoiceError("Perekam suara tidak tersedia (%s)." % e)
    try:
        stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK)
        stream.start()
    except Exception as e:
        raise VoiceError("Mikrofon tidak bisa dibuka (%s). Pastikan mikrofon terpasang dan izin mikrofon "
                         "Windows untuk aplikasi desktop aktif." % e)

    def read_chunk():
        data, _overflow = stream.read(CHUNK)
        return bytes(data)

    def close():
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    return read_chunk, close


class Listener:
    def __init__(self, cfg, opener=open_microphone, recognizer=transcribe):
        self.cfg = cfg or {}
        self.opener = opener
        self.recognizer = recognizer

    def listen(self, should_stop=lambda: False, on_status=lambda s: None, on_level=None):
        """Rekam lalu kenali. Mengembalikan teks ("" bila tidak ada ucapan). Melempar VoiceError."""
        read_chunk, close = self.opener()
        try:
            on_status("listening")
            pcm = record_until_silence(
                read_chunk, should_stop,
                max_seconds=float(self.cfg.get("max_seconds", 15)),
                silence_seconds=float(self.cfg.get("silence_seconds", 1.2)),
                on_level=on_level)
        finally:
            close()
        if not pcm or should_stop():
            return ""
        on_status("recognizing")
        return self.recognizer(pcm, self.cfg.get("language", "id-ID"))
