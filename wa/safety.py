import re

# Kata pada nama tombol/elemen yang membuat aksi perlu konfirmasi manusia.
DEFAULT_RISKY_WORDS = [
    "delete", "hapus", "remove", "erase", "wipe", "format", "uninstall", "install", "instal",
    "send", "kirim", "submit", "post", "publish",
    "pay", "payment", "bayar", "pembayaran", "checkout", "purchase", "buy", "beli", "order", "pesan sekarang",
    "transfer", "withdraw", "tarik",
    "shutdown", "shut down", "restart", "sign out", "log out", "logout",
    "overwrite", "timpa", "empty recycle bin", "kosongkan",
]

SENSITIVE_FIELD_WORDS = ["password", "kata sandi", "sandi", "pin", "otp", "cvv", "cvc", "kode verifikasi"]

CHAT_CONTEXT_WORDS = [
    "whatsapp", "telegram", "message", "pesan", "chat", "mail", "gmail", "outlook",
    "compose", "discord", "slack", "messenger", "instagram", "line",
]

FINANCE_TITLE_WORDS = ["bank", "payment", "pembayaran", "checkout", "paypal", "internet banking", "mbanking", "m-banking"]

RISKY_HOTKEYS = [
    {"alt", "f4"},
    {"ctrl", "shift", "delete"},
    {"shift", "delete"},
    {"win", "l"},
]
RISKY_KEYS = {"delete", "del"}

CLICK_ACTIONS = ("click", "double_click", "right_click")


def _word_regex(words):
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.I)


class Safety:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        words = DEFAULT_RISKY_WORDS + list(cfg.get("extra_risky_words", []))
        self.risky = _word_regex(words)
        self.sensitive = _word_regex(SENSITIVE_FIELD_WORDS)
        self.chat = _word_regex(CHAT_CONTEXT_WORDS)
        self.finance = _word_regex(FINANCE_TITLE_WORDS)
        self.confirm_all = bool(cfg.get("confirm_every_action", False))

    def assess(self, action, element=None, window_title=""):
        """Kembalikan (perlu_konfirmasi, alasan)."""
        if self.confirm_all:
            return True, "mode konfirmasi semua aksi sedang aktif"
        reasons = []
        kind = action.get("action")
        name = (element or {}).get("name", "") or ""

        if str(action.get("risk", "")).lower() == "high":
            reasons.append("AI menandai aksi ini berisiko")

        if kind in CLICK_ACTIONS:
            m = self.risky.search(name)
            if m:
                reasons.append('tombol "%s" mengandung kata "%s"' % (name, m.group(1)))
            if self.finance.search(window_title or ""):
                reasons.append("jendela terkait pembayaran/perbankan")

        if kind == "type":
            if element and self.sensitive.search(name):
                reasons.append('kolom "%s" tampak sensitif' % name)
            if action.get("enter") and self.chat.search(window_title or ""):
                reasons.append("menekan Enter di aplikasi pesan/email dapat langsung mengirim")
            if action.get("enter") and self.finance.search(window_title or ""):
                reasons.append("jendela terkait pembayaran/perbankan")

        if kind == "hotkey":
            keys = {str(k).lower() for k in action.get("keys", [])}
            for combo in RISKY_HOTKEYS:
                if combo <= keys:
                    reasons.append("pintasan keyboard %s berisiko" % "+".join(sorted(combo)))
                    break

        if kind == "press" and str(action.get("key", "")).lower() in RISKY_KEYS:
            reasons.append("menekan tombol Delete")

        return (bool(reasons), "; ".join(reasons))
