import copy
import json
import os

DEFAULTS = {
    "llm": {
        # LM Studio: http://localhost:1234/v1   |   Ollama: http://localhost:11434/v1
        "base_url": "http://localhost:1234/v1",
        # Kosongkan untuk LM Studio (memakai model yang sedang dimuat).
        # Untuk Ollama isi nama model, contoh: "qwen2.5:3b"
        "model": "",
        "api_key": "local",
        "temperature": 0.1,
        "max_tokens": 400,
        "timeout": 240,
        # true = kirim screenshot bertanda nomor (butuh model vision). false = hanya teks (lebih ringan)
        "use_vision": False,
        "vision_max_width": 1024,
    },
    "agent": {
        "max_steps": 25,
        "step_delay": 0.8,
        "max_elements": 70,
        "history_window": 8,
        "auto_reflect": True,
        "stop_hotkey": "f12",
        "skill_match_threshold": 0.6,
    },
    "update": {
        # Repo GitHub tempat rilis Asisten PC diterbitkan (format pemilik/nama-repo, harus publik).
        "repo": "Mazehax/AI-Hazmat",
        "check_on_start": True,
        # Hanya untuk repo PRIVATE: token GitHub read-only (Contents: Read) khusus repo itu. Isi di laptop, jangan di-commit.
        "token": "",
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


def load_config(path):
    user = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    return _merge(DEFAULTS, user)


def save_default_config(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2, ensure_ascii=False)
