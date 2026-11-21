"""Skill bawaan: pekerjaan umum di PC yang dijalankan langsung lewat kode (cepat dan akurat),
bukan dengan mengeklik layar. AI memanggilnya dengan {"action": "skill", "name": "...", "args": {...}}.
"""
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile

OUT_FOLDER = "AsistenPC"
WIN = sys.platform == "win32"

# nama: (cara pakai singkat untuk prompt, risiko: low | high | check)
SPECS = {
    "make_excel_report": ('{"kind": "keuangan|penjualan|anggaran|inventaris", "path": optional, "active": true to write into the '
                          'Excel window that is already open} - ready-made sample report with data, formulas and chart', "low"),
    "make_excel": ('{"path": optional, "sheets": [{"name": "...", "rows": [[header...],[...]], "chart": "bar|line|pie" optional}]} '
                   '- cells starting with "=" are formulas', "low"),
    "excel_write_active": ('{"rows": [[...],[...]], "start": "A1"} - write into the Excel sheet that is already open', "low"),
    "make_word": ('{"path": optional, "title": "...", "paragraphs": ["# Heading", "text", "- bullet"]}', "low"),
    "make_pptx": ('{"path": optional, "title": "...", "slides": [{"title": "...", "bullets": ["..."]}]}', "low"),
    "list_files": '{"path": "Desktop", "pattern": optional "*.pdf"}',
    "find_files": '{"query": "name part", "folder": optional}',
    "read_file": '{"path": "..."} - txt, csv, json, docx, xlsx, pdf',
    "write_file": '{"path": "...", "text": "...", "append": false}',
    "make_folder": '{"path": "..."}',
    "copy_file": '{"src": "...", "dst": "..."}',
    "move_file": '{"src": "...", "dst": "..."} - also renames',
    "organize_folder": '{"path": "Downloads"} - sorts files into subfolders by type',
    "zip_folder": '{"path": "...", "dst": optional}',
    "recycle": '{"path": "..."} - moves to Recycle Bin',
    "open_path": '{"path": "file, folder or https URL"}',
    "web_search": '{"query": "..."} - opens a browser search',
    "download_file": '{"url": "https://...", "path": optional}',
    "screenshot": '{"path": optional}',
    "clipboard": '{"mode": "get" or "set", "text": "..."}',
    "system_info": "{} - RAM, disk, battery, time",
    "current_time": "{}",
    "volume": '{"direction": "up|down|mute", "steps": 5}',
    "lock_screen": "{}",
    "run_powershell": '{"command": "..."} - last resort for anything else; always asks the user',
}
RISK = {
    "make_excel_report": "low", "make_excel": "low", "excel_write_active": "low", "make_word": "low", "make_pptx": "low",
    "list_files": "low", "find_files": "low", "read_file": "low", "write_file": "check", "make_folder": "low",
    "copy_file": "check", "move_file": "check", "organize_folder": "high", "zip_folder": "low", "recycle": "high",
    "open_path": "low", "web_search": "low", "download_file": "high", "screenshot": "low", "clipboard": "low",
    "system_info": "low", "current_time": "low", "volume": "low", "lock_screen": "low", "run_powershell": "high",
}
SKILL_NAMES = tuple(SPECS)
for _k, _v in list(SPECS.items()):
    if isinstance(_v, tuple):
        SPECS[_k] = _v[0]

TYPE_FOLDERS = {
    "Dokumen": {".doc", ".docx", ".pdf", ".txt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".odt"},
    "Gambar": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".wmv"},
    "Musik": {".mp3", ".wav", ".flac", ".m4a", ".aac"},
    "Arsip": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Program": {".exe", ".msi", ".apk"},
}
SHORTCUTS = {"desktop": "Desktop", "documents": "Documents", "dokumen": "Documents", "downloads": "Downloads",
             "unduhan": "Downloads", "pictures": "Pictures", "gambar": "Pictures", "music": "Music", "videos": "Videos"}


class SkillError(Exception):
    pass


def catalog_text():
    lines = []
    for name in SKILL_NAMES:
        lines.append("- %s %s" % (name, SPECS[name]))
    return "\n".join(lines)


def _num(v):
    if isinstance(v, str):
        t = v.strip().replace(",", "") if re.fullmatch(r"-?[\d.,]+", v.strip()) else None
        if t:
            try:
                return float(t) if "." in t else int(t)
            except ValueError:
                return v
    return v


class Skills:
    def __init__(self, out_dir=None, runner=None, opener=None):
        self.home = os.path.expanduser("~")
        self.out_dir = out_dir or os.path.join(self.home, "Documents", OUT_FOLDER)
        self.runner = runner or subprocess.run
        self.opener = opener  # fungsi(path) untuk membuka hasil; None = buka otomatis di Windows

    # ---------- util ----------
    def resolve(self, p, default_name=None, ext=None):
        p = str(p or "").strip().strip('"')
        if not p:
            if not default_name:
                raise SkillError("path wajib diisi")
            p = default_name
        p = os.path.expandvars(os.path.expanduser(p))
        if not os.path.isabs(p):
            first, _, rest = p.replace("\\", "/").partition("/")
            if first.lower() in SHORTCUTS:
                p = os.path.join(self.home, SHORTCUTS[first.lower()], *([rest] if rest else []))
            else:
                p = os.path.join(self.out_dir, p)
        if ext and not p.lower().endswith(ext):
            p += ext
        return os.path.normpath(p)

    def _unique(self, path):
        base, ext = os.path.splitext(path)
        n, cand = 2, path
        while os.path.exists(cand):
            cand = "%s_%d%s" % (base, n, ext)
            n += 1
        return cand

    def _prepare(self, args, default_name, ext):
        path = self._unique(self.resolve(args.get("path"), default_name, ext))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _open(self, path):
        if self.opener:
            self.opener(path)
        elif WIN and not os.environ.get("ASISTEN_NO_OPEN"):
            try:
                os.startfile(path)
            except OSError:
                pass

    def _ps(self, command, timeout=30):
        if not WIN:
            raise SkillError("Perintah ini hanya tersedia di Windows.")
        try:
            r = self.runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
                            creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            raise SkillError("Perintah terlalu lama (lebih dari %d detik)." % timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            raise SkillError(out[:400] or "perintah gagal")
        return out

    # ---------- penilaian risiko ----------
    def assess(self, action):
        """(perlu_konfirmasi, alasan) untuk aksi skill."""
        name = action.get("name")
        args = action.get("args") or {}
        kind = RISK.get(name, "high")
        if kind == "high":
            detail = args.get("command") or args.get("path") or args.get("url") or ""
            return True, "skill %s dapat mengubah/menghapus data atau menjalankan perintah: %s" % (name, str(detail)[:80])
        if kind == "check":
            try:
                if name == "write_file":
                    p = self.resolve(args.get("path"))
                    if os.path.exists(p) and not args.get("append"):
                        return True, "akan menimpa file yang sudah ada: %s" % p
                else:
                    d = self.resolve(args.get("dst"))
                    if os.path.exists(d):
                        return True, "tujuan sudah ada dan akan ditimpa: %s" % d
            except SkillError:
                pass
        return False, ""

    def run(self, name, args):
        if name not in SPECS:
            raise SkillError("skill tidak dikenal: %s" % name)
        if not isinstance(args, dict):
            raise SkillError("args harus berupa objek")
        fn = getattr(self, "s_" + name)
        try:
            return str(fn(args))
        except SkillError:
            raise
        except ImportError as e:
            raise SkillError("library belum terpasang: %s" % e)
        except (OSError, KeyError, ValueError, TypeError) as e:
            raise SkillError("%s: %s" % (type(e).__name__, e))

    # ---------- berkas ----------
    def s_list_files(self, a):
        folder = self.resolve(a.get("path"), "Desktop")
        if not os.path.isdir(folder):
            raise SkillError("folder tidak ada: %s" % folder)
        import fnmatch
        pat = a.get("pattern")
        rows = []
        for n in sorted(os.listdir(folder)):
            if pat and not fnmatch.fnmatch(n.lower(), str(pat).lower()):
                continue
            full = os.path.join(folder, n)
            rows.append("%s%s" % (n, "/" if os.path.isdir(full) else " (%d KB)" % (os.path.getsize(full) // 1024)))
        return "%s: %d item\n%s" % (folder, len(rows), "\n".join(rows[:40]) + ("\n..." if len(rows) > 40 else ""))

    def s_find_files(self, a):
        q = str(a.get("query", "")).lower().strip()
        if not q:
            raise SkillError("query wajib diisi")
        root = self.resolve(a.get("folder"), "Documents") if a.get("folder") else self.home
        found, start = [], time.time()
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith((".", "$")) and x not in ("AppData", "node_modules")]
            for f in files:
                if q in f.lower():
                    found.append(os.path.join(d, f))
            if len(found) >= 30 or time.time() - start > 20:
                break
        return "%d berkas ditemukan\n%s" % (len(found), "\n".join(found[:30]))

    def s_read_file(self, a):
        p = self.resolve(a.get("path"))
        if not os.path.isfile(p):
            raise SkillError("file tidak ada: %s" % p)
        ext = os.path.splitext(p)[1].lower()
        if ext == ".docx":
            import docx
            text = "\n".join(x.text for x in docx.Document(p).paragraphs)
        elif ext in (".xlsx", ".xlsm"):
            import openpyxl
            ws = openpyxl.load_workbook(p, data_only=True).worksheets[0]
            text = "\n".join(" | ".join("" if c is None else str(c) for c in row) for row in ws.iter_rows(max_row=40, values_only=True))
        elif ext == ".pdf":
            from pypdf import PdfReader
            text = "\n".join((pg.extract_text() or "") for pg in PdfReader(p).pages[:5])
        else:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(4000)
        return text[:1800] or "(kosong)"

    def s_write_file(self, a):
        p = self.resolve(a.get("path"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a" if a.get("append") else "w", encoding="utf-8") as f:
            f.write(str(a.get("text", "")))
        return "tersimpan: %s" % p

    def s_make_folder(self, a):
        p = self.resolve(a.get("path"))
        os.makedirs(p, exist_ok=True)
        return "folder siap: %s" % p

    def s_copy_file(self, a):
        s, d = self.resolve(a.get("src")), self.resolve(a.get("dst"))
        if not os.path.exists(s):
            raise SkillError("sumber tidak ada: %s" % s)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        return "disalin ke %s" % d

    def s_move_file(self, a):
        s, d = self.resolve(a.get("src")), self.resolve(a.get("dst"))
        if not os.path.exists(s):
            raise SkillError("sumber tidak ada: %s" % s)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(s, d)
        return "dipindah ke %s" % d

    def s_organize_folder(self, a):
        folder = self.resolve(a.get("path"), "Downloads")
        if not os.path.isdir(folder):
            raise SkillError("folder tidak ada: %s" % folder)
        counts = {}
        for n in os.listdir(folder):
            full = os.path.join(folder, n)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(n)[1].lower()
            cat = next((c for c, exts in TYPE_FOLDERS.items() if ext in exts), "Lainnya")
            dest_dir = os.path.join(folder, cat)
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(full, self._unique(os.path.join(dest_dir, n)))
            counts[cat] = counts.get(cat, 0) + 1
        return "dirapikan: " + (", ".join("%s %d" % kv for kv in counts.items()) or "tidak ada berkas")

    def s_zip_folder(self, a):
        src = self.resolve(a.get("path"))
        if not os.path.exists(src):
            raise SkillError("tidak ada: %s" % src)
        dst = self._unique(self.resolve(a.get("dst"), os.path.basename(src.rstrip("\\/")) + ".zip", ".zip"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            if os.path.isdir(src):
                for d, _dirs, files in os.walk(src):
                    for f in files:
                        full = os.path.join(d, f)
                        z.write(full, os.path.relpath(full, os.path.dirname(src)))
            else:
                z.write(src, os.path.basename(src))
        return "zip dibuat: %s" % dst

    def s_recycle(self, a):
        p = self.resolve(a.get("path"))
        if not os.path.exists(p):
            raise SkillError("tidak ada: %s" % p)
        fn = "DeleteDirectory" if os.path.isdir(p) else "DeleteFile"
        esc = p.replace("'", "''")
        self._ps("Add-Type -AssemblyName Microsoft.VisualBasic; "
                 "[Microsoft.VisualBasic.FileIO.FileSystem]::%s('%s','OnlyErrorDialogs','SendToRecycleBin')" % (fn, esc))
        return "dipindah ke Recycle Bin: %s" % p

    # ---------- dokumen ----------
    def _excel_book(self, sheets):
        import openpyxl
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        if not isinstance(sheets, list) or not sheets:
            raise SkillError("sheets wajib berisi minimal satu sheet")
        for si, spec in enumerate(sheets, 1):
            rows = spec.get("rows") or []
            ws = wb.create_sheet(re.sub(r"[\\/*?:\[\]]", "", str(spec.get("name") or "Sheet%d" % si))[:31])
            for r in rows:
                ws.append([_num(c) for c in (r if isinstance(r, list) else [r])])
            if rows:
                for c in ws[1]:
                    c.font = Font(bold=True, color="FFFFFF")
                    c.fill = PatternFill("solid", fgColor="1F4E78")
                    c.alignment = Alignment(horizontal="center", vertical="center")
                ws.freeze_panes = "A2"
                headers = [str(c.value or "") for c in ws[1]]
                for row in ws.iter_rows(min_row=2):
                    for c in row:
                        if isinstance(c.value, (int, float)) or (isinstance(c.value, str) and c.value.startswith("=")):
                            h = headers[c.column - 1]
                            c.number_format = "0.0%" if h.endswith("%") else "#,##0"
                for i in range(1, ws.max_column + 1):
                    width = max(len(str(c.value if c.value is not None else "")) for c in ws[get_column_letter(i)])
                    ws.column_dimensions[get_column_letter(i)].width = min(40, max(10, width + 3))
            chart = spec.get("chart")
            if chart and len(rows) > 1 and ws.max_column > 1:
                cfg = chart if isinstance(chart, dict) else {"type": chart}
                kind = str(cfg.get("type", "bar")).lower()
                ch = {"line": LineChart, "pie": PieChart}.get(kind, BarChart)()
                ch.title = cfg.get("title") or ws.title
                last = len(rows)
                for col in cfg.get("series") or list(range(2, ws.max_column + 1)):
                    ch.add_data(Reference(ws, min_col=col, min_row=1, max_row=last), titles_from_data=True)
                    if kind == "pie":
                        break
                ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=last))
                ch.height, ch.width = 8.5, 18
                ws.add_chart(ch, "%s2" % get_column_letter(ws.max_column + 2))
        return wb

    def s_make_excel(self, a):
        wb = self._excel_book(a.get("sheets"))
        p = self._prepare(a, "laporan.xlsx", ".xlsx")
        wb.save(p)
        self._open(p)
        return "Excel dibuat: %s" % p

    def _report_sheets(self, kind):
        rnd = random.Random(7)
        months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
        if kind == "keuangan":
            lr = [["Bulan", "Pendapatan", "HPP", "Laba Kotor", "Biaya Operasional", "Laba Bersih", "Margin %"]]
            ak = [["Bulan", "Kas Masuk", "Kas Keluar", "Arus Bersih", "Saldo Akhir"]]
            base = 150_000_000
            for i, m in enumerate(months, 2):
                rev = int(base * (1 + i * 0.02) * rnd.uniform(0.9, 1.1) // 100000 * 100000)
                cogs = int(rev * rnd.uniform(0.55, 0.62) // 100000 * 100000)
                opex = int(rev * rnd.uniform(0.18, 0.24) // 100000 * 100000)
                lr.append([m, rev, cogs, "=B%d-C%d" % (i, i), opex, "=D%d-E%d" % (i, i), "=IFERROR(F%d/B%d,0)" % (i, i)])
                cin = int(rev * rnd.uniform(0.9, 1.0) // 100000 * 100000)
                cout = int((cogs + opex) * rnd.uniform(0.95, 1.05) // 100000 * 100000)
                ak.append([m, cin, cout, "=B%d-C%d" % (i, i), ("=D%d" % i) if i == 2 else "=E%d+D%d" % (i - 1, i)])
            lr.append(["Total"] + ["=SUM(%s2:%s13)" % (c, c) for c in "BCDEF"] + ["=IFERROR(F14/B14,0)"])
            return [{"name": "Laba Rugi", "rows": lr, "chart": {"type": "bar", "title": "Pendapatan dan Laba Bersih", "series": [2, 6]}},
                    {"name": "Arus Kas", "rows": ak, "chart": {"type": "line", "title": "Saldo Kas", "series": [5]}}]
        if kind == "penjualan":
            prods = ["Produk A", "Produk B", "Produk C", "Produk D", "Produk E"]
            rows = [["Produk", "Harga", "Terjual", "Omzet", "Kontribusi %"]]
            for i, pr in enumerate(prods, 2):
                rows.append([pr, rnd.randrange(20, 200) * 1000, rnd.randrange(50, 400), "=B%d*C%d" % (i, i), "=D%d/D$7" % i])
            rows.append(["Total", "", "=SUM(C2:C6)", "=SUM(D2:D6)", "=SUM(E2:E6)"])
            return [{"name": "Penjualan", "rows": rows, "chart": {"type": "bar", "title": "Omzet per Produk", "series": [4]}}]
        if kind == "anggaran":
            cats = ["Gaji", "Sewa", "Listrik dan Air", "Pemasaran", "Perlengkapan", "Transportasi", "Lain-lain"]
            rows = [["Kategori", "Anggaran", "Realisasi", "Selisih", "Terpakai %"]]
            for i, c in enumerate(cats, 2):
                bud = rnd.randrange(5, 60) * 1_000_000
                rows.append([c, bud, int(bud * rnd.uniform(0.7, 1.12) // 10000 * 10000), "=B%d-C%d" % (i, i), "=C%d/B%d" % (i, i)])
            n = len(cats) + 2
            rows.append(["Total", "=SUM(B2:B%d)" % (n - 1), "=SUM(C2:C%d)" % (n - 1), "=B%d-C%d" % (n, n), "=C%d/B%d" % (n, n)])
            return [{"name": "Anggaran", "rows": rows, "chart": {"type": "bar", "title": "Anggaran vs Realisasi", "series": [2, 3]}}]
        if kind == "inventaris":
            items = ["Kertas A4", "Tinta Printer", "Mouse", "Keyboard", "Kabel HDMI", "Monitor", "Kursi Kantor"]
            rows = [["Barang", "Stok", "Harga Satuan", "Nilai Stok", "Status"]]
            for i, it in enumerate(items, 2):
                rows.append([it, rnd.randrange(2, 90), rnd.randrange(10, 900) * 1000, "=B%d*C%d" % (i, i),
                             '=IF(B%d<10,"Perlu restok","Aman")' % i])
            rows.append(["Total", "=SUM(B2:B8)", "", "=SUM(D2:D8)", ""])
            return [{"name": "Inventaris", "rows": rows, "chart": {"type": "bar", "title": "Nilai Stok", "series": [4]}}]
        raise SkillError("kind harus salah satu: keuangan, penjualan, anggaran, inventaris")

    def s_make_excel_report(self, a):
        kind = str(a.get("kind", "keuangan")).lower()
        sheets = self._report_sheets(kind)
        if a.get("active"):
            try:
                return self._write_sheets_com(sheets)
            except SkillError as e:
                note = " (Excel yang terbuka tidak bisa diisi langsung: %s; dibuat sebagai file baru)" % e
        else:
            note = ""
        wb = self._excel_book(sheets)
        p = self._prepare(a, "laporan_%s.xlsx" % kind, ".xlsx")
        wb.save(p)
        self._open(p)
        return "Laporan %s dibuat: %s%s" % (kind, p, note)

    def _com_excel(self):
        if not WIN:
            raise SkillError("hanya di Windows")
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            return win32com.client.GetActiveObject("Excel.Application")
        except Exception as e:
            raise SkillError("Excel tidak sedang terbuka (%s)" % e)

    def _write_sheets_com(self, sheets):
        xl = self._com_excel()
        try:
            wb = xl.ActiveWorkbook
            if wb is None:
                raise SkillError("tidak ada workbook yang terbuka")
            for si, spec in enumerate(sheets):
                ws = xl.ActiveSheet if si == 0 else wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
                try:
                    ws.Name = str(spec["name"])[:31]
                except Exception:
                    pass
                self._com_fill(ws, spec["rows"], "A1")
                self._com_chart(ws, spec)
            return "Laporan ditulis ke Excel yang sedang terbuka (%d sheet)." % len(sheets)
        except SkillError:
            raise
        except Exception as e:
            raise SkillError(str(e))

    def _com_fill(self, ws, rows, start):
        from_col = re.match(r"([A-Za-z]+)(\d+)", start or "A1")
        col0 = 0
        for ch in from_col.group(1).upper():
            col0 = col0 * 26 + ord(ch) - 64
        row0 = int(from_col.group(2))
        for r, row in enumerate(rows):
            for c, val in enumerate(row if isinstance(row, list) else [row]):
                val = _num(val)
                cell = ws.Cells(row0 + r, col0 + c)
                if isinstance(val, str) and val.startswith("="):
                    cell.Formula = val
                else:
                    cell.Value = val
        header = ws.Range(ws.Cells(row0, col0), ws.Cells(row0, col0 + len(rows[0]) - 1))
        header.Font.Bold = True
        header.Interior.Color = 0x784E1F  # biru tua (BGR)
        header.Font.Color = 0xFFFFFF
        ws.Range(ws.Cells(row0, col0), ws.Cells(row0 + len(rows) - 1, col0 + len(rows[0]) - 1)).Columns.AutoFit()
        for c, h in enumerate(rows[0]):
            rng = ws.Range(ws.Cells(row0 + 1, col0 + c), ws.Cells(row0 + len(rows) - 1, col0 + c))
            rng.NumberFormat = "0.0%" if str(h).endswith("%") else "#,##0"

    def _com_chart(self, ws, spec):
        ch = spec.get("chart")
        rows = spec.get("rows") or []
        if not ch or len(rows) < 2:
            return
        try:
            cfg = ch if isinstance(ch, dict) else {"type": ch}
            kind = {"line": 4, "pie": 5}.get(str(cfg.get("type", "bar")).lower(), 51)
            ncol = len(rows[0])
            left = ws.Cells(2, ncol + 2).Left
            shape = ws.Shapes.AddChart2(201, kind, left, ws.Cells(2, 1).Top, 480, 280)
            chart = shape.Chart
            chart.SetSourceData(ws.Range(ws.Cells(1, 1), ws.Cells(len(rows), ncol)))
            chart.HasTitle = True
            chart.ChartTitle.Text = cfg.get("title") or spec.get("name", "")
        except Exception:
            pass  # grafik hanya pemanis; data tetap tertulis

    def s_excel_write_active(self, a):
        rows = a.get("rows")
        if not isinstance(rows, list) or not rows:
            raise SkillError("rows wajib berisi minimal satu baris")
        xl = self._com_excel()
        try:
            self._com_fill(xl.ActiveSheet, [r if isinstance(r, list) else [r] for r in rows], str(a.get("start", "A1")))
        except Exception as e:
            raise SkillError(str(e))
        return "%d baris ditulis ke Excel yang terbuka." % len(rows)

    def s_make_word(self, a):
        import docx
        doc = docx.Document()
        if a.get("title"):
            doc.add_heading(str(a["title"]), 0)
        for para in a.get("paragraphs") or []:
            t = str(para)
            if t.startswith("### "):
                doc.add_heading(t[4:], 3)
            elif t.startswith("## "):
                doc.add_heading(t[3:], 2)
            elif t.startswith("# "):
                doc.add_heading(t[2:], 1)
            elif t.startswith(("- ", "* ")):
                doc.add_paragraph(t[2:], style="List Bullet")
            else:
                doc.add_paragraph(t)
        p = self._prepare(a, "dokumen.docx", ".docx")
        doc.save(p)
        self._open(p)
        return "Word dibuat: %s" % p

    def s_make_pptx(self, a):
        from pptx import Presentation
        prs = Presentation()
        slides = a.get("slides") or []
        first = prs.slides.add_slide(prs.slide_layouts[0])
        first.shapes.title.text = str(a.get("title") or "Presentasi")
        for s in slides:
            sl = prs.slides.add_slide(prs.slide_layouts[1])
            sl.shapes.title.text = str(s.get("title", ""))
            body = sl.placeholders[1].text_frame
            bullets = [str(b) for b in (s.get("bullets") or [])] or [""]
            body.text = bullets[0]
            for b in bullets[1:]:
                body.add_paragraph().text = b
        p = self._prepare(a, "presentasi.pptx", ".pptx")
        prs.save(p)
        self._open(p)
        return "PowerPoint dibuat: %s (%d slide)" % (p, len(slides) + 1)

    # ---------- web dan sistem ----------
    def s_open_path(self, a):
        target = str(a.get("path") or a.get("url") or "").strip()
        if re.match(r"https?://", target, re.I):
            webbrowser.open(target)
            return "dibuka di browser: %s" % target
        p = self.resolve(target)
        if not os.path.exists(p):
            raise SkillError("tidak ada: %s" % p)
        self._open(p)
        return "dibuka: %s" % p

    def s_web_search(self, a):
        q = str(a.get("query", "")).strip()
        if not q:
            raise SkillError("query wajib diisi")
        url = "https://www.google.com/search?q=" + urllib.parse.quote(q)
        webbrowser.open(url)
        return "pencarian dibuka di browser: %s" % q

    def s_download_file(self, a):
        url = str(a.get("url", ""))
        if not re.match(r"https?://", url, re.I):
            raise SkillError("url harus diawali http:// atau https://")
        name = os.path.basename(urllib.parse.urlparse(url).path) or "unduhan"
        p = self._unique(self.resolve(a.get("path"), os.path.join("Downloads", name)))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "AsistenPC"})
        with urllib.request.urlopen(req, timeout=60) as r, open(p, "wb") as f:
            shutil.copyfileobj(r, f)
        return "diunduh: %s (%d KB)" % (p, os.path.getsize(p) // 1024)

    def s_screenshot(self, a):
        import mss
        from PIL import Image
        p = self._prepare(a, os.path.join("Pictures", OUT_FOLDER, "layar_%s.png" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")), ".png")
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])
        Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX").save(p)
        return "screenshot tersimpan: %s" % p

    def s_clipboard(self, a):
        import pyperclip
        if str(a.get("mode", "get")).lower() == "set":
            pyperclip.copy(str(a.get("text", "")))
            return "teks disalin ke clipboard"
        return pyperclip.paste()[:1500] or "(clipboard kosong)"

    def s_system_info(self, a):
        import platform
        info = ["Sistem: %s %s" % (platform.system(), platform.release()),
                "Waktu: %s" % datetime.datetime.now().strftime("%A %d-%m-%Y %H:%M")]
        try:
            from .lmstudio_setup import system_ram_gb
            ram = system_ram_gb()
            if ram:
                info.append("RAM: %.1f GB" % ram)
        except Exception:
            pass
        du = shutil.disk_usage(self.home)
        info.append("Disk: kosong %.0f GB dari %.0f GB" % (du.free / 1024 ** 3, du.total / 1024 ** 3))
        if WIN:
            try:
                b = self._ps("(Get-CimInstance Win32_Battery | Select-Object -First 1 -ExpandProperty EstimatedChargeRemaining)", 10)
                if b:
                    info.append("Baterai: %s%%" % b)
            except SkillError:
                pass
        return "\n".join(info)

    def s_current_time(self, a):
        return datetime.datetime.now().strftime("%A, %d %B %Y, %H:%M")

    def s_volume(self, a):
        import pyautogui
        d = str(a.get("direction", "up")).lower()
        if d == "mute":
            pyautogui.press("volumemute")
            return "volume dibisukan/dibunyikan"
        key = "volumeup" if d == "up" else "volumedown"
        n = max(1, min(25, int(a.get("steps", 5))))
        pyautogui.press(key, presses=n)
        return "volume %s %d langkah" % (d, n)

    def s_lock_screen(self, a):
        if not WIN:
            raise SkillError("hanya di Windows")
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return "layar dikunci"

    def s_run_powershell(self, a):
        cmd = str(a.get("command", "")).strip()
        if not cmd:
            raise SkillError("command wajib diisi")
        return self._ps(cmd, 30)[:1200] or "(selesai tanpa keluaran)"
