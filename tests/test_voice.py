import array
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wa import voice, winui
from wa.voice import CHUNK, Listener, VoiceError, record_until_silence, rms


def chunk(level):
    return array.array("h", [level, -level] * (CHUNK // 2)).tobytes()


def feeder(levels):
    it = iter(levels)
    return lambda: chunk(next(it, 0))


class VoiceTests(unittest.TestCase):
    def test_rms(self):
        self.assertEqual(rms(b""), 0.0)
        self.assertAlmostEqual(rms(chunk(1000)), 1000.0, delta=1)

    def test_records_speech_and_stops_after_silence(self):
        levels = [20] * 4 + [4000] * 10 + [20] * 20
        pcm = record_until_silence(feeder(levels), silence_seconds=1.0)
        self.assertGreater(len(pcm), 10 * len(chunk(1)))
        self.assertLess(len(pcm), 30 * len(chunk(1)))  # berhenti setelah hening, tidak merekam semua

    def test_no_speech_returns_empty(self):
        self.assertEqual(record_until_silence(feeder([30] * 100), start_timeout=2.0), b"")

    def test_stop_requested(self):
        self.assertEqual(record_until_silence(feeder([4000] * 50), should_stop=lambda: True), b"")

    def test_noisy_room_needs_louder_speech(self):
        pcm = record_until_silence(feeder([800] * 40), start_timeout=2.0)  # bising stabil bukan ucapan
        self.assertEqual(pcm, b"")

    def test_max_seconds(self):
        levels = [20] * 4 + [4000] * 500
        pcm = record_until_silence(feeder(levels), max_seconds=3.0)
        self.assertLessEqual(len(pcm), 31 * len(chunk(1)))

    def test_listener_flow(self):
        closed = []
        status = []
        levels = [20] * 4 + [4000] * 6 + [20] * 20
        lst = Listener({"silence_seconds": 1.0, "language": "id-ID"},
                       opener=lambda: (feeder(levels), lambda: closed.append(1)),
                       recognizer=lambda pcm, lang: "buka notepad (%s)" % lang)
        self.assertEqual(lst.listen(on_status=status.append), "buka notepad (id-ID)")
        self.assertEqual(status, ["listening", "recognizing"])
        self.assertEqual(closed, [1])

    def test_listener_returns_empty_without_speech(self):
        lst = Listener({}, opener=lambda: (feeder([20] * 100), lambda: None),
                       recognizer=lambda *a: self.fail("tidak boleh dipanggil"))
        self.assertEqual(lst.listen(), "")

    def test_missing_microphone_is_clear_error(self):
        def bad():
            raise VoiceError("Mikrofon tidak bisa dibuka")
        with self.assertRaises(VoiceError):
            Listener({}, opener=bad).listen()

    def test_winui_is_safe_off_windows(self):
        if not winui.IS_WIN:
            self.assertFalse(winui.notify("a", "b"))
            self.assertFalse(winui.force_foreground(object()))
            winui.beep()
            winui.flash(object())


class RecognizerTests(unittest.TestCase):
    class FakeOffline:
        def __init__(self, text="", fail=False):
            self.text, self.fail, self.calls = text, fail, 0

        def transcribe(self, pcm, lang):
            self.calls += 1
            if self.fail:
                raise RuntimeError("model rusak")
            return self.text

    def test_google_first_then_offline(self):
        off = self.FakeOffline("buka excel")
        r = voice.Recognizer({}, google=lambda p, l: "", offline=off)
        self.assertEqual(r(b"x", "id-ID"), "buka excel")
        self.assertEqual(r.last_engine, "offline")
        r2 = voice.Recognizer({}, google=lambda p, l: "buka notepad", offline=off)
        self.assertEqual(r2(b"x", "id-ID"), "buka notepad")
        self.assertEqual(r2.last_engine, "Google")
        self.assertEqual(off.calls, 1)  # offline tidak dipanggil bila Google berhasil

    def test_google_error_falls_back_and_reports(self):
        def bad(p, l):
            raise VoiceError("tidak ada internet")
        off = self.FakeOffline("")
        r = voice.Recognizer({}, google=bad, offline=off)
        self.assertEqual(r(b"x", "id-ID"), "")
        self.assertTrue(any("internet" in e for e in r.last_errors))
        off2 = self.FakeOffline(fail=True)
        r = voice.Recognizer({}, google=bad, offline=off2)
        self.assertEqual(r(b"x", "id-ID"), "")
        self.assertTrue(any("offline" in e for e in r.last_errors))

    def test_engine_choice(self):
        off = self.FakeOffline("offline saja")
        r = voice.Recognizer({"engine": "offline"}, google=lambda p, l: self.fail("tidak boleh"), offline=off)
        self.assertEqual(r(b"x", "id-ID"), "offline saja")

    def test_save_wav_roundtrip(self):
        import tempfile
        import wave
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a", "x.wav")
            voice.save_wav(chunk(1000) * 3, p)
            with wave.open(p) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (16000, 1, 4800))


class OfflineModelTests(unittest.TestCase):
    def test_ensure_model_downloads_missing_files_only(self):
        import tempfile
        from wa import stt_offline
        got = []
        def fake(url, dest, prog, stop):
            got.append(url.rsplit("/", 1)[1])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").close()
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(stt_offline.is_ready(d, "base"))
            path = stt_offline.ensure_model(d, "base", downloader=fake)
            self.assertTrue(stt_offline.is_ready(d, "base"))
            self.assertEqual(sorted(got), sorted(stt_offline.FILES))
            self.assertEqual(stt_offline.ensure_model(d, "base", downloader=fake), path)
            self.assertEqual(len(got), 4)  # tidak mengunduh ulang

    def test_download_failure_is_clear(self):
        import tempfile
        from wa import stt_offline
        def bad(url, dest, prog, stop):
            raise OSError("putus")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(stt_offline.OfflineError):
                stt_offline.ensure_model(d, "tiny", downloader=bad)


class IconTests(unittest.TestCase):
    def test_icons_draw(self):
        from wa.icons import make_icon
        for name in ("play", "stop", "mic"):
            img = make_icon(name, "#ffffff", 32)
            self.assertEqual(img.size, (32, 32))
            self.assertIsNotNone(img.getbbox())  # ada gambarnya
        with self.assertRaises(ValueError):
            make_icon("x")


if __name__ == "__main__":
    unittest.main()
