"""Memori jangka panjang agen.

Model lokal tidak dilatih ulang. "Belajar" di sini artinya agen menyimpan:
  - pelajaran (lessons): catatan singkat tentang cara mengoperasikan aplikasi
  - skill: urutan aksi yang pernah berhasil, bisa diputar ulang tanpa AI
  - fakta (facts): preferensi/info pengguna, misalnya folder kerja
Semua disimpan sebagai file JSON biasa di folder data/ sehingga bisa dilihat dan diedit manusia.
"""
import json
import os
import re
import threading
import time

STOPWORDS = set("yang dan di ke dari untuk the a an to of and in on for with lalu kemudian tolong please".split())
SECRET_PATTERN = re.compile(
    r"\d{9,}|password|passwd|kata sandi|sandi|(?<![a-z0-9])(?:otp|pin)(?![a-z0-9])|token|api[_ -]?key|cvv|cvc", re.I)


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in STOPWORDS}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _norm(text):
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (text or "").lower())).strip()


class Memory:
    MAX_LESSONS = 300
    MAX_LESSON_CHARS = 200

    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self._lock = threading.RLock()
        self.lessons = self._load("lessons.json", [])
        self.skills = self._load("skills.json", [])
        self.facts = self._load("facts.json", {})

    # ---------- penyimpanan ----------
    def _path(self, name):
        return os.path.join(self.folder, name)

    def _load(self, name, default):
        try:
            with open(self._path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def _save(self, name, data):
        tmp = self._path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path(name))

    # ---------- pelajaran ----------
    def add_lesson(self, text, app="", source="auto"):
        text = (text or "").strip()
        if not text or len(text) < 8:
            return False
        if SECRET_PATTERN.search(text):
            return False
        text = text[: self.MAX_LESSON_CHARS]
        key = _norm(text)
        with self._lock:
            for item in self.lessons:
                if _norm(item["text"]) == key:
                    item["count"] = item.get("count", 1) + 1
                    self._save("lessons.json", self.lessons)
                    return False
            self.lessons.append({
                "text": text, "app": (app or "").strip(), "count": 1,
                "created": int(time.time()), "source": source,
            })
            if len(self.lessons) > self.MAX_LESSONS:
                self.lessons.sort(key=lambda x: (x.get("source") == "user", x.get("count", 1), x.get("created", 0)))
                self.lessons = self.lessons[len(self.lessons) - self.MAX_LESSONS:]
            self._save("lessons.json", self.lessons)
        return True

    def find_lessons(self, goal, app="", k=5):
        query = tokens(goal) | tokens(app)
        scored = []
        with self._lock:
            for item in self.lessons:
                item_tokens = tokens(item["text"]) | tokens(item.get("app", ""))
                score = jaccard(query, item_tokens)
                if app and item.get("app") and item["app"].lower() in app.lower():
                    score += 0.3
                if item.get("source") == "user":
                    score += 0.05
                if score > 0.08:
                    scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    def delete_lesson(self, index):
        with self._lock:
            if 0 <= index < len(self.lessons):
                self.lessons.pop(index)
                self._save("lessons.json", self.lessons)
                return True
        return False

    # ---------- skill ----------
    def save_skill(self, name, goal, steps):
        name = (name or "").strip()
        if not name or not steps:
            return False
        with self._lock:
            self.skills = [s for s in self.skills if s["name"].lower() != name.lower()]
            self.skills.append({
                "name": name, "goal": goal, "steps": steps,
                "created": int(time.time()), "runs": 0, "ok_runs": 0,
            })
            self._save("skills.json", self.skills)
        return True

    def find_skill(self, goal):
        query = tokens(goal)
        best, best_score = None, 0.0
        with self._lock:
            for s in self.skills:
                score = jaccard(query, tokens(s["goal"]))
                if score > best_score:
                    best, best_score = s, score
        return best, best_score

    def list_skills(self):
        with self._lock:
            return list(self.skills)

    def delete_skill(self, name):
        with self._lock:
            before = len(self.skills)
            self.skills = [s for s in self.skills if s["name"].lower() != name.lower()]
            if len(self.skills) != before:
                self._save("skills.json", self.skills)
                return True
        return False

    def record_run(self, name, ok):
        with self._lock:
            for s in self.skills:
                if s["name"].lower() == name.lower():
                    s["runs"] = s.get("runs", 0) + 1
                    if ok:
                        s["ok_runs"] = s.get("ok_runs", 0) + 1
            self._save("skills.json", self.skills)

    # ---------- fakta ----------
    def set_fact(self, key, value):
        key, value = (key or "").strip(), (value or "").strip()
        if not key or not value or SECRET_PATTERN.search(key + " " + value):
            return False
        with self._lock:
            self.facts[key] = value
            self._save("facts.json", self.facts)
        return True

    def delete_fact(self, key):
        with self._lock:
            if key in self.facts:
                del self.facts[key]
                self._save("facts.json", self.facts)
                return True
        return False

    def facts_text(self):
        with self._lock:
            return "\n".join("- %s: %s" % (k, v) for k, v in self.facts.items())
