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
