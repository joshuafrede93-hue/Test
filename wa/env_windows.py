"""Lapisan Windows: membaca elemen UI lewat UI Automation dan menjalankan aksi mouse/keyboard.

File ini hanya bisa dijalankan di Windows. Impor library Windows dilakukan di dalam __init__
supaya modul lain (dan tes) tetap bisa diimpor di sistem lain.
"""
import base64
import ctypes
import io
import os
import time
from ctypes import wintypes

from .core import Observation

INTERACTIVE = {
    "Button", "Edit", "MenuItem", "ListItem", "CheckBox", "RadioButton", "ComboBox", "TabItem",
    "Hyperlink", "TreeItem", "DataItem", "SplitButton", "Document", "Slider", "Spinner",
}
INFO_ONLY = {"Text"}
MAX_TEXT_ELEMENTS = 12

KEY_ALIASES = {
    "control": "ctrl", "windows": "win", "super": "win", "return": "enter", "escape": "esc",
    "del": "delete", "pgup": "pageup", "pgdn": "pagedown",
}

GW_HWNDNEXT = 2
SW_RESTORE = 9


def _user32():
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = ctypes.c_void_p
    u.GetWindow.restype = ctypes.c_void_p
    u.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.IsIconic.argtypes = [ctypes.c_void_p]
    u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return u


class WindowsEnv:
    def __init__(self, llm_cfg, agent_cfg, own_titles=()):
        import pyautogui
        import pyperclip
        from pywinauto import Desktop

        self.pg = pyautogui
        self.clip = pyperclip
        self.Desktop = Desktop
        self.pg.FAILSAFE = True  # gerakkan mouse ke pojok kiri atas layar untuk menghentikan paksa
        self.pg.PAUSE = 0.15
        self.llm_cfg = llm_cfg
        self.agent_cfg = agent_cfg
        self.own_titles = set(own_titles)
        self.u = _user32()
        self._elements = {}
        self._last_hwnd = 0
        self._win_rect = None

    # ---------- thread ----------
    def thread_init(self):
        """Panggil di awal thread pekerja: UI Automation memakai COM yang perlu diinisialisasi per thread."""
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

    # ---------- jendela ----------
    def _title(self, hwnd):
        n = self.u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        self.u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _is_own(self, hwnd):
        pid = wintypes.DWORD(0)
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()

    def _usable(self, hwnd):
        if not hwnd or not self.u.IsWindowVisible(hwnd) or self.u.IsIconic(hwnd):
            return False
        if self._is_own(hwnd):
            return False
        if self.u.GetWindowTextLengthW(hwnd) == 0:
            return False
        try:
            cloaked = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 14, ctypes.byref(cloaked),
                                                       ctypes.sizeof(cloaked))
            if cloaked.value:
                return False
        except Exception:
            pass
        return True

    def _target_hwnd(self):
        hwnd = self.u.GetForegroundWindow()
        for _ in range(80):
            if hwnd and self._usable(hwnd):
                return hwnd
            hwnd = self.u.GetWindow(hwnd, GW_HWNDNEXT) if hwnd else 0
            if not hwnd:
                break
        return self._last_hwnd or 0

    def _refocus(self):
        """Kalau fokus tertinggal di jendela Asisten PC, kembalikan ke jendela target."""
        fg = self.u.GetForegroundWindow()
        if fg and self._is_own(fg) and self._last_hwnd:
            self.u.ShowWindow(self._last_hwnd, SW_RESTORE)
            self.u.SetForegroundWindow(self._last_hwnd)
            time.sleep(0.3)

    def _open_window_titles(self):
        titles = []
        try:
            for w in self.Desktop(backend="uia").windows():
                try:
                    t = (w.window_text() or "").strip()
                except Exception:
                    continue
                if t and t not in self.own_titles and t not in titles and t != "Program Manager":
                    titles.append(t[:60])
        except Exception:
            pass
        return titles

    # ---------- observasi ----------
    def observe(self):
        from pywinauto.uia_element_info import UIAElementInfo

        hwnd = self._target_hwnd()
        self._last_hwnd = hwnd
        title = self._title(hwnd) if hwnd else ""
        interactive, texts = [], []
        self._win_rect = None
        if hwnd:
            root = UIAElementInfo(handle_or_elem=hwnd)
            try:
                wr = root.rectangle
                self._win_rect = (wr.left, wr.top, wr.right, wr.bottom)
            except Exception:
                pass
            max_el = int(self.agent_cfg.get("max_elements", 70))
            for info in root.descendants(cache_enable=True):
                try:
                    ctype = info.control_type
                    if ctype not in INTERACTIVE and ctype not in INFO_ONLY:
                        continue
                    name = (info.name or "").strip().replace("\n", " ")[:60]
                    if ctype in INFO_ONLY and not name:
                        continue
                    if not info.visible:
                        continue
                    if ctype in INTERACTIVE and not info.enabled:
                        continue
                    r = info.rectangle
                    if r.right - r.left <= 2 or r.bottom - r.top <= 2:
                        continue
                    item = {"name": name or "(tanpa nama)", "ctype": ctype,
                            "rect": (r.left, r.top, r.right, r.bottom)}
                except Exception:
                    continue
                (texts if ctype in INFO_ONLY else interactive).append(item)
                if len(interactive) > max_el * 3:
                    break
            interactive.sort(key=lambda e: (e["rect"][1] // 12, e["rect"][0]))
            chosen = interactive[: max(10, max_el - MAX_TEXT_ELEMENTS)] + texts[:MAX_TEXT_ELEMENTS]
            chosen.sort(key=lambda e: (e["rect"][1] // 12, e["rect"][0]))
        else:
            chosen = []

        self._elements = {}
        public = []
        for i, item in enumerate(chosen, 1):
            self._elements[i] = item
            public.append({"id": i, "name": item["name"], "ctype": item["ctype"]})

        shot = None
        if self.llm_cfg.get("use_vision"):
            try:
                shot = self._screenshot()
            except Exception:
                shot = None
        return Observation(window_title=title, windows=self._open_window_titles(), elements=public,
                           screenshot_b64=shot)

    def _screenshot(self):
        import mss
        from PIL import Image, ImageDraw

        with mss.mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        max_w = int(self.llm_cfg.get("vision_max_width", 1024))
        scale = min(1.0, max_w / float(img.width))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        draw = ImageDraw.Draw(img)
        for i, item in self._elements.items():
            l, t, r, b = item["rect"]
            box = [(l - mon["left"]) * scale, (t - mon["top"]) * scale,
                   (r - mon["left"]) * scale, (b - mon["top"]) * scale]
            draw.rectangle(box, outline=(255, 0, 0), width=1)
            draw.text((box[0] + 2, box[1] + 1), str(i), fill=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    # ---------- aksi ----------
    def _center(self, element_id):
        item = self._elements.get(element_id)
        if not item:
            raise KeyError("id %s tidak ada" % element_id)
        l, t, r, b = item["rect"]
        return int((l + r) / 2), int((t + b) / 2)

    def _paste(self, text, replace=False):
        old = None
        try:
            old = self.clip.paste()
        except Exception:
            pass
        self.clip.copy(text)
        time.sleep(0.1)
        if replace:
            self.pg.hotkey("ctrl", "a")
            time.sleep(0.1)
        self.pg.hotkey("ctrl", "v")
        time.sleep(0.2)
        if old is not None:
            try:
                self.clip.copy(old)
            except Exception:
                pass

    @staticmethod
    def _key(name):
        k = str(name).lower().strip()
        return KEY_ALIASES.get(k, k)

    def perform(self, action):
        self._refocus()
        kind = action["action"]
        pg = self.pg

        if kind in ("click", "double_click", "right_click"):
            x, y = self._center(action["id"])
            pg.moveTo(x, y, duration=0.1)
            if kind == "double_click":
                pg.click(x, y, clicks=2, interval=0.1)
            elif kind == "right_click":
                pg.click(x, y, button="right")
            else:
                pg.click(x, y)
            return True, "ok"

        if kind == "type":
            if action.get("id") is not None:
                x, y = self._center(action["id"])
                pg.click(x, y)
                time.sleep(0.2)
            self._paste(action["text"], replace=bool(action.get("replace")))
            if action.get("enter"):
                pg.press("enter")
            return True, "ok"

        if kind == "hotkey":
            pg.hotkey(*[self._key(k) for k in action["keys"]])
            return True, "ok"

        if kind == "press":
            pg.press(self._key(action["key"]))
            return True, "ok"

        if kind == "scroll":
            if self._win_rect:
                l, t, r, b = self._win_rect
                pg.moveTo(int((l + r) / 2), int((t + b) / 2))
            pg.scroll(max(-30, min(30, int(action["amount"]))))
            return True, "ok"

        if kind == "open_app":
            pg.press("win")
            time.sleep(0.8)
            self._paste(action["name"])
            time.sleep(1.0)
            pg.press("enter")
            time.sleep(2.0)
            return True, "ok"

        if kind == "focus_window":
            want = action["title"].lower()
            for w in self.Desktop(backend="uia").windows():
                try:
                    text = (w.window_text() or "")
                except Exception:
                    continue
                if want in text.lower() and text not in self.own_titles:
                    w.set_focus()
                    time.sleep(0.5)
                    return True, "ok"
            return False, 'jendela dengan judul "%s" tidak ditemukan' % action["title"]

        if kind == "wait":
            time.sleep(float(action.get("seconds", 1)))
            return True, "ok"

        return False, "aksi tidak didukung: %s" % kind
