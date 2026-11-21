import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ASISTEN_NO_OPEN"] = "1"

from wa.skills import SKILL_NAMES, Skills, SkillError, catalog_text


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Skills(out_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_skill_has_implementation_and_risk(self):
        from wa.skills import RISK
        for n in SKILL_NAMES:
            self.assertTrue(hasattr(self.s, "s_" + n), n)
            self.assertIn(n, RISK)
        self.assertIn("make_excel_report", catalog_text())

    def test_excel_reports_all_kinds(self):
        import openpyxl
        for kind in ("keuangan", "penjualan", "anggaran", "inventaris"):
            msg = self.s.run("make_excel_report", {"kind": kind})
            path = msg.split(": ", 1)[1]
            wb = openpyxl.load_workbook(path)
            ws = wb.worksheets[0]
            self.assertGreater(ws.max_row, 5)
            self.assertTrue(ws._charts, kind)  # ada grafik
            self.assertTrue(any(isinstance(c.value, str) and c.value.startswith("=") for r in ws.iter_rows() for c in r))

    def test_keuangan_formulas_are_consistent(self):
        import openpyxl
        path = self.s.run("make_excel_report", {"kind": "keuangan"}).split(": ", 1)[1]
        ws = openpyxl.load_workbook(path)["Laba Rugi"]
        self.assertEqual(ws["D2"].value, "=B2-C2")
        self.assertEqual(ws["B14"].value, "=SUM(B2:B13)")
        ak = openpyxl.load_workbook(path)["Arus Kas"]
        self.assertEqual(ak["E3"].value, "=E2+D3")

    def test_make_excel_custom_and_unique_names(self):
        spec = {"path": "x.xlsx", "sheets": [{"name": "Data", "rows": [["Nama", "Nilai"], ["A", "1,200"], ["B", "300"], ["T", "=SUM(B2:B3)"]],
                                              "chart": "bar"}]}
        p1 = self.s.run("make_excel", spec).split(": ", 1)[1]
        p2 = self.s.run("make_excel", spec).split(": ", 1)[1]
        self.assertNotEqual(p1, p2)  # tidak menimpa
        import openpyxl
        self.assertEqual(openpyxl.load_workbook(p1).active["B2"].value, 1200)
        with self.assertRaises(SkillError):
            self.s.run("make_excel", {"sheets": []})

    def test_word_and_pptx(self):
        import docx
        from pptx import Presentation
        p = self.s.run("make_word", {"title": "Laporan", "paragraphs": ["# Bab 1", "isi", "- poin"]}).split(": ", 1)[1]
        self.assertEqual(len(docx.Document(p).paragraphs), 4)
        q = self.s.run("make_pptx", {"title": "T", "slides": [{"title": "S1", "bullets": ["a", "b"]}]}).split(": ", 1)[1].split(" (")[0]
        self.assertEqual(len(Presentation(q).slides), 2)

    def test_file_operations_and_organize(self):
        d = self.tmp.name
        for n in ("a.pdf", "b.jpg", "c.xyz"):
            open(os.path.join(d, n), "w").write("x")
        self.assertIn("3 item", self.s.run("list_files", {"path": d}))
        self.assertIn("Dokumen 1", self.s.run("organize_folder", {"path": d}))
        self.assertTrue(os.path.exists(os.path.join(d, "Gambar", "b.jpg")))
        self.assertTrue(os.path.exists(os.path.join(d, "Lainnya", "c.xyz")))
        self.assertIn("1 berkas", self.s.run("find_files", {"query": "b.jpg", "folder": d}))
        self.s.run("write_file", {"path": os.path.join(d, "n.txt"), "text": "halo"})
        self.assertEqual(self.s.run("read_file", {"path": os.path.join(d, "n.txt")}), "halo")
        self.s.run("move_file", {"src": os.path.join(d, "n.txt"), "dst": os.path.join(d, "m.txt")})
        self.assertTrue(os.path.exists(os.path.join(d, "m.txt")))
        z = self.s.run("zip_folder", {"path": os.path.join(d, "Gambar")})
        self.assertIn("zip dibuat", z)

    def test_risk_assessment(self):
        d = self.tmp.name
        self.assertTrue(self.s.assess({"name": "run_powershell", "args": {"command": "dir"}})[0])
        self.assertTrue(self.s.assess({"name": "recycle", "args": {"path": "x"}})[0])
        self.assertTrue(self.s.assess({"name": "organize_folder", "args": {}})[0])
        self.assertFalse(self.s.assess({"name": "make_excel_report", "args": {}})[0])
        f = os.path.join(d, "e.txt")
        self.assertFalse(self.s.assess({"name": "write_file", "args": {"path": f}})[0])
        open(f, "w").write("x")
        self.assertTrue(self.s.assess({"name": "write_file", "args": {"path": f}})[0])  # menimpa
        self.assertFalse(self.s.assess({"name": "write_file", "args": {"path": f, "append": True}})[0])

    def test_unknown_and_bad_args(self):
        with self.assertRaises(SkillError):
            self.s.run("nope", {})
        with self.assertRaises(SkillError):
            self.s.run("read_file", {"path": os.path.join(self.tmp.name, "tidak-ada.txt")})
        with self.assertRaises(SkillError):
            self.s.run("run_powershell", {"command": "dir"})  # bukan Windows

    def test_desktop_shortcut_resolves_to_home(self):
        self.assertTrue(self.s.resolve("Desktop/a.txt").endswith(os.path.join("Desktop", "a.txt")))
        self.assertTrue(self.s.resolve("a.txt").startswith(self.tmp.name))


if __name__ == "__main__":
    unittest.main()
