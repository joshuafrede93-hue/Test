import copy
import json
import os

CONFIG_VERSION = 2

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "llm": {
        # LM Studio: http://localhost:1234/v1   |   Ollama: http://localhost:11434/v1
        "base_url": "http://localhost:1234/v1",
        # Kosongkan untuk LM Studio (memakai model yang sedang dimuat).
        # Untuk Ollama isi nama model, contoh: "qwen2.5:3b"
        "model": "",
        "api_key": "local",
        "temperature": 0.1,
        # Balasan agen hanya satu objek JSON pendek, jadi 300 token cukup (lebih kecil = lebih cepat).
        "max_tokens": 300,
        "timeout": 300,
        # Matikan mode "berpikir" Qwen3 dan paksa keluaran JSON: jauh lebih cepat dan lebih rapi.
        "no_think": True,
        "json_mode": True,
        # Otomatis pindah ke model yang pas untuk RAM laptop bila model yang termuat terlalu besar/lambat.
        "auto_switch_model": True,
        # true = kirim screenshot bertanda nomor (butuh model vision). false = hanya teks (lebih ringan)
        "use_vision": False,
        "vision_max_width": 1024,
    },
    "agent": {
        "max_steps": 25,
        "step_delay": 0.5,
        "max_elements": 45,
        "history_window": 6,
        "auto_reflect": True,
        "stop_hotkey": "f12",
        "skill_match_threshold": 0.6,
    },
    "voice": {
        "enabled": True,
        "language": "id-ID",
        "hotkey": "f9",
        # true = setelah ucapan dikenali, langsung dijalankan sebagai perintah
        "auto_run": True,
        "max_seconds": 15,
        "silence_seconds": 1.2,
    },
    "safety": {
        "extra_risky_words": [],
        "confirm_every_action": False,
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


# Nilai lama (versi < 2) yang membuat AI lambat; dibuang agar default baru berlaku.
_OLD_SLOW_KEYS = {"llm": ("max_tokens", "timeout"), "agent": ("max_elements", "history_window", "step_delay")}


def migrate(user):
    """Naikkan config lama ke versi sekarang. Mengembalikan (dict, berubah?)."""
    if int(user.get("config_version", 1)) >= CONFIG_VERSION:
        return user, False
    user = copy.deepcopy(user)
    for section, keys in _OLD_SLOW_KEYS.items():
        if isinstance(user.get(section), dict):
            for k in keys:
                user[section].pop(k, None)
    user["config_version"] = CONFIG_VERSION
    return user, True


def load_config(path):
    user = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        user, changed = migrate(user)
        if changed:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(user, f, indent=2, ensure_ascii=False)
            except OSError:
                pass
    return _merge(DEFAULTS, user)


def save_default_config(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2, ensure_ascii=False)
