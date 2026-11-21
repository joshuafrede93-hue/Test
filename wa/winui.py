"""Bantuan jendela Windows: bawa jendela ke depan, kedipkan ikon taskbar, bunyi, dan notifikasi.
Semua fungsi aman dipanggil di sistem lain (tidak melakukan apa-apa) dan tidak pernah melempar error."""
import os
import sys

IS_WIN = sys.platform == "win32"


def _hwnd_of(widget):
    import ctypes
    child = widget.winfo_id()
    parent = ctypes.windll.user32.GetParent(child)
    return parent or child


def force_foreground(widget):
    """Windows menolak jendela latar belakang merebut fokus; trik AttachThreadInput + Alt mengatasinya."""
    if not IS_WIN:
        return False
    try:
        import ctypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = _hwnd_of(widget)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        my_thread = kernel32.GetCurrentThreadId()
        attached = False
        if fg_thread and fg_thread != my_thread:
            attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
        user32.keybd_event(0x12, 0, 0, 0)  # tekan-lepas Alt membuka kunci SetForegroundWindow
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_TOPMOST, tanpa geser/ubah ukuran
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
        if attached:
            user32.AttachThreadInput(my_thread, fg_thread, False)
        return ok
    except Exception:
        return False


def flash(widget, count=5):
    """Kedipkan tombol di taskbar sampai jendela dibuka."""
    if not IS_WIN:
        return
    try:
        import ctypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                        ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), _hwnd_of(widget), 0x3 | 0xC, count, 0)  # ALL | TIMERNOFG
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def beep(kind="alert"):
    if not IS_WIN:
        return
    try:
        import winsound
        if kind == "tick":
            winsound.Beep(880, 90)
        else:
            winsound.MessageBeep(0x30)  # MB_ICONEXCLAMATION
    except Exception:
        pass


def _show_toast(title, message):
    try:
        from winotify import Notification, audio
        toast = Notification(app_id="Asisten PC", title=title[:60], msg=message[:220], duration="short")
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception:
        pass


def notify(title, message):
    """Notifikasi Windows (toast) di pojok layar. Berjalan di thread terpisah supaya tidak membekukan jendela;
    diam-diam dilewati bila tidak tersedia."""
    if not IS_WIN or os.environ.get("ASISTEN_NO_TOAST"):
        return False
    import threading
    threading.Thread(target=_show_toast, args=(title, message), daemon=True).start()
    return True
