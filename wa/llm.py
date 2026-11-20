import json
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def extract_json(text):
    """Ambil objek JSON pertama dari balasan model (toleran terhadap teks tambahan)."""
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
                    return obj if isinstance(obj, dict) else None
    return None


class LocalLLM:
    """Klien untuk server lokal yang kompatibel dengan OpenAI API (LM Studio, Ollama, llama.cpp)."""

    def __init__(self, cfg):
        self.cfg = cfg
        # Abaikan proxy sistem: server model ada di komputer sendiri.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _url(self, path):
        return self.cfg["base_url"].rstrip("/") + path

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + str(self.cfg.get("api_key", "local")),
        }

    def chat(self, messages, image_b64=None):
        msgs = [dict(m) for m in messages]
        if image_b64 and self.cfg.get("use_vision"):
            last = msgs[-1]
            last["content"] = [
                {"type": "text", "text": last["content"]},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
            ]
        payload = {
            "messages": msgs,
            "temperature": self.cfg.get("temperature", 0.1),
            "max_tokens": self.cfg.get("max_tokens", 400),
            "stream": False,
        }
        if self.cfg.get("model"):
            payload["model"] = self.cfg["model"]
        req = urllib.request.Request(
            self._url("/chat/completions"),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=self.cfg.get("timeout", 240)) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise LLMError("Server model membalas error %s: %s" % (e.code, body))
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise LLMError("Tidak bisa menghubungi server model di %s (%s). "
                           "Pastikan LM Studio/Ollama sudah menyala." % (self.cfg["base_url"], e))
        except json.JSONDecodeError as e:
            raise LLMError("Balasan server model bukan JSON: %s" % e)
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError("Format balasan server model tidak dikenal.")
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()

    def ping(self):
        req = urllib.request.Request(self._url("/models"), headers=self._headers())
        try:
            with self._opener.open(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return False, str(e)
        names = [m.get("id", "?") for m in data.get("data", [])]
        return True, (", ".join(names) if names else "(belum ada model yang dimuat)")
