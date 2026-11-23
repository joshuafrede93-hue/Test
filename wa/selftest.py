"""Uji mandiri untuk .exe hasil build: memastikan semua library ikut terbungkus dan fitur inti benar-benar jalan.
Dipakai CI: AsistenPC.exe --selftest hasil.txt [--selftest-model]."""
import importlib
import os
import tempfile
import traceback

MODULES = ["customtkinter", "pywinauto", "pyperclip", "mss", "PIL", "keyboard", "speech_recognition", "sounddevice",
           "winotify", "openpyxl", "docx", "pptx", "pypdf", "win32com.client", "pythoncom", "faster_whisper",
           "ctranslate2", "numpy"]


def run(out_path, with_model=False):
    lines, ok = [], True

    def step(name, fn):
        nonlocal ok
        try:
            detail = fn()
            lines.append("OK   %s%s" % (name, (" - " + str(detail)) if detail else ""))
        except Exception as e:
            ok = False
            lines.append("FAIL %s: %s" % (name, e))
            lines.append(traceback.format_exc(limit=3))

    for m in MODULES:
        step("import " + m, lambda m=m: importlib.import_module(m) and None)

    tmp = tempfile.mkdtemp(prefix="asisten_selftest_")
    os.environ["ASISTEN_NO_OPEN"] = "1"
    from .skills import Skills
    sk = Skills(out_dir=tmp)

    def excel():
        import openpyxl
        path = sk.run("make_excel_report", {"kind": "transaksi", "rows": 100}).split(": ", 1)[1].split(" (")[0]
        ws = openpyxl.load_workbook(path)["Transaksi"]
        assert ws.max_row == 101, "baris=%s" % ws.max_row
        return "101 baris"

    step("skill excel 100 baris", excel)
    step("skill word", lambda: sk.run("make_word", {"title": "T", "paragraphs": ["# A", "isi"]}) and None)
    step("skill pptx", lambda: sk.run("make_pptx", {"title": "T", "slides": [{"title": "S", "bullets": ["a"]}]}) and None)

    def icons():
        from .icons import make_icon
        for n in ("play", "stop", "mic"):
            make_icon(n)
    step("ikon", icons)

    def schema():
        from .core import ACTION_SCHEMA
        assert len(ACTION_SCHEMA["anyOf"]) >= 13
    step("skema aksi", schema)

    if with_model:
        def whisper():
            from .stt_offline import OfflineRecognizer
            rec = OfflineRecognizer(tmp, "tiny", log=lambda m: lines.append("     " + m))
            text = rec.transcribe(b"\x00\x00" * 32000, "id-ID")  # 2 detik hening
            assert isinstance(text, str)
            return "hening -> %r" % text
        step("whisper offline (unduh + jalan)", whisper)

    lines.append("RESULT OK" if ok else "RESULT FAIL")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1
