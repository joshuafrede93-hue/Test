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


def _action_variant(name, required=None, optional=None):
    """Satu bentuk aksi. Urutan properti tetap (thought, action, parameter, risk) agar tata bahasa keluaran sederhana."""
    props = {"thought": {"type": "string", "maxLength": 140}, "action": {"type": "string", "enum": [name]}}
    props.update(required or {})
    props.update(optional or {})
    props["risk"] = {"type": "string", "enum": ["low", "high"]}
    req = ["thought", "action"] + list((required or {}).keys()) + ["risk"]
    return {"type": "object", "properties": props, "required": req}


def build_action_schema(skill_names=()):
    """Skema JSON per aksi: parameter wajib benar-benar wajib, jadi model tidak bisa lupa 'name' atau 'title'."""
    string, integer = {"type": "string"}, {"type": "integer"}
    variants = [_action_variant(n, {"id": integer}) for n in ("click", "double_click", "right_click")]
    variants += [
        _action_variant("type", {"text": string}, {"id": integer, "enter": {"type": "boolean"}, "replace": {"type": "boolean"}}),
        _action_variant("hotkey", {"keys": {"type": "array", "items": string, "minItems": 1}}),
        _action_variant("press", {"key": string}),
        _action_variant("scroll", {"amount": integer}),
        _action_variant("open_app", {"name": string}),
        _action_variant("focus_window", {"title": string}),
        _action_variant("wait", {"seconds": {"type": "number"}}),
        _action_variant("ask_user", {"question": string}),
        _action_variant("done", {"summary": string}),
        _action_variant("fail", {"reason": string}),
    ]
    skill_name = {"type": "string", "enum": list(skill_names)} if skill_names else string
    variants.append(_action_variant("skill", {"name": skill_name, "args": {"type": "object"}}))
    return {"anyOf": variants}


ACTION_SCHEMA = build_action_schema()


class LocalLLM:
    """Klien untuk server lokal yang kompatibel dengan OpenAI API (LM Studio, Ollama, llama.cpp)."""

    def __init__(self, cfg):
        self.cfg = cfg
        # Abaikan proxy sistem: server model ada di komputer sendiri.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._extras_ok = True  # jadi False bila server menolak parameter tambahan (json schema, dll)

    def _url(self, path):
        return self.cfg["base_url"].rstrip("/") + path

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + str(self.cfg.get("api_key", "local")),
        }

    def _build_payload(self, messages, image_b64, max_tokens, schema, extras):
        msgs = [dict(m) for m in messages]
        last = msgs[-1]
        if self.cfg.get("no_think", True) and isinstance(last["content"], str):
            last["content"] = last["content"] + "\n/no_think"  # perintah resmi Qwen3: lewati mode berpikir
        if image_b64 and self.cfg.get("use_vision"):
            last["content"] = [
                {"type": "text", "text": last["content"]},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
            ]
        payload = {
            "messages": msgs,
            "temperature": self.cfg.get("temperature", 0.1),
            "max_tokens": int(max_tokens or self.cfg.get("max_tokens", 300)),
            "stream": False,
        }
        if self.cfg.get("model"):
            payload["model"] = self.cfg["model"]
        if extras and schema and self.cfg.get("json_mode", True):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "action", "strict": True, "schema": schema},
            }
        return payload

    def _post(self, payload):
        req = urllib.request.Request(
            self._url("/chat/completions"),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with self._opener.open(req, timeout=self.cfg.get("timeout", 300)) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat(self, messages, image_b64=None, max_tokens=None, schema=None):
        extras = self._extras_ok
        try:
            try:
                data = self._post(self._build_payload(messages, image_b64, max_tokens, schema, extras))
            except urllib.error.HTTPError as e:
                if e.code == 400 and extras and schema:
                    # Server lama tidak mengenal response_format: ulangi tanpa itu dan ingat pilihannya.
                    self._extras_ok = False
                    data = self._post(self._build_payload(messages, image_b64, max_tokens, schema, False))
                else:
                    raise
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
