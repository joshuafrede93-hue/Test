import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wa import updater
from wa.lmstudio_setup import (Setup, SetupError, choose_model, parse_models, parse_port,
                               update_config_file)


class FakeUI:
    def __init__(self, yes=True):
        self.logs = []
        self.yes = yes
        self.asked = []

    def log(self, m):
        self.logs.append(m)

    def confirm(self, m):
        self.asked.append(m)
        return self.yes

    def should_stop(self):
        return False


class FakeLLM:
    def __init__(self, cfg, ping_results):
        self.cfg = cfg
        self.pings = list(ping_results)

    def ping(self):
        return self.pings.pop(0) if len(self.pings) > 1 else self.pings[0]

    def chat(self, messages, image_b64=None):
        return "siap"


class FakeLms:
    """Meniru perilaku lms: ls kosong sampai get dipanggil."""

    def __init__(self, downloaded=False, get_fails_first=False):
        self.downloaded = downloaded
        self.get_fails_first = get_fails_first
        self.calls = []
        self.get_count = 0
        self.dl_urls = []

    def __call__(self, args, timeout=120, stdin_text=None, on_line=None):
        self.calls.append(args[1:])
        cmd = args[1]
        if cmd == "ls":
            if self.downloaded:
                return 0, json.dumps([{"type": "llm", "modelKey": "qwen/qwen3-14b"},
                                      {"type": "embedding", "modelKey": "nomic-embed"}])
            return 0, "[]"
        if cmd == "get":
            self.get_count += 1
            if self.get_fails_first and self.get_count == 1:
                return 1, "not found"
            self.downloaded = True
            return 0, "done"
        if cmd == "load":
            return 0, "loaded"
        if cmd == "server" and args[2] == "status":
            return 0, "The server is running on port 1234."
        if cmd == "server":
            return 0, "started"
        return 1, "?"


def make_setup(ui, runner, ram=16.0, pings=None, tmp=None, downloader=None):
    cfg = {"base_url": "http://localhost:1234/v1", "model": "", "max_tokens": 400, "timeout": 240}
    llm = FakeLLM(cfg, pings or [(True, "qwen/qwen3-14b")])
    path = os.path.join(tmp, "config.json")
    with open(path, "w") as f:
        json.dump({"llm": {"base_url": "x"}, "agent": {"max_steps": 9}}, f)
    def fake_dl(url, dest, prog, stop):
        runner.downloaded = True
        runner.dl_urls.append(url)
        return 9

    s = Setup(ui, llm, cfg, path, runner=runner, ram_gb=ram, sleep=lambda s: None,
              finder_lms=lambda: "lms.exe", finder_exe=lambda: "LM Studio.exe", launcher=lambda e: None,
              downloader=downloader or fake_dl, models_path=os.path.join(tmp, "models"), free_gb=100)
    return s, cfg, path


class SetupTests(unittest.TestCase):
    def test_choose_model_by_ram(self):
        self.assertIn("14B", choose_model(15.8)["label"])
        self.assertIn("8B", choose_model(12)["label"])
        self.assertIn("4B", choose_model(8)["label"])
        self.assertIn("3B", choose_model(4)["label"])

    def test_parse_models_skips_embeddings(self):
        out = json.dumps([{"type": "llm", "modelKey": "a/b"}, {"type": "embedding", "modelKey": "e/f"}])
        self.assertEqual(parse_models(out), ["a/b"])
        self.assertEqual(parse_models("garbage"), [])

    def test_parse_port(self):
        self.assertEqual(parse_port("Server: ON, port: 1235"), 1235)
        self.assertEqual(parse_port("listening on localhost:4321"), 4321)
        self.assertIsNone(parse_port("off"))

    def test_full_flow_downloads_loads_and_updates_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui, runner = FakeUI(), FakeLms()
            s, cfg, path = make_setup(ui, runner, tmp=tmp)
            msg = s.run_all()
            self.assertIn("Setup selesai", msg)
            self.assertEqual(ui.asked, [])  # sepenuhnya otomatis, tanpa pertanyaan
            self.assertTrue(runner.dl_urls[0].endswith("Qwen3-14B-Q4_K_M.gguf"))
            self.assertIn("load", [c[0] for c in runner.calls])
            saved = json.load(open(path))
            self.assertEqual(saved["agent"]["max_steps"], 9)  # pengaturan lain utuh
            self.assertEqual(saved["llm"]["base_url"], "http://localhost:1234/v1")
            self.assertGreaterEqual(saved["llm"]["max_tokens"], 1500)
            self.assertGreaterEqual(cfg["timeout"], 600)

    def test_skips_download_when_model_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui, runner = FakeUI(), FakeLms(downloaded=True)
            s, _cfg, _p = make_setup(ui, runner, tmp=tmp)
            s.run_all()
            self.assertEqual(runner.dl_urls, [])
            self.assertNotIn("get", [c[0] for c in runner.calls])

    def test_falls_back_to_lms_get_when_direct_download_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui, runner = FakeUI(), FakeLms()

            def bad(url, dest, prog, stop):
                raise SetupError("403")
            s, _c, _p = make_setup(ui, runner, tmp=tmp, downloader=bad)
            s.run_all()
            self.assertEqual(runner.get_count, 1)

    def test_not_enough_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui, runner = FakeUI(), FakeLms()
            s, _c, _p = make_setup(ui, runner, tmp=tmp)
            s.free_gb = 3
            with self.assertRaises(SetupError):
                s.run_all()
            self.assertEqual(runner.dl_urls, [])

    def test_installs_when_app_missing_even_if_lms_folder_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui, runner = FakeUI(), FakeLms(downloaded=True)
            s, _c, _p = make_setup(ui, runner, tmp=tmp)
            state = {"installed": False}
            s.find_exe = lambda: "LM Studio.exe" if state["installed"] else None
            calls = []

            def run(args, timeout=120, stdin_text=None, on_line=None):
                if args[0] == "winget":
                    calls.append(args)
                    state["installed"] = True
                    return 0, "ok"
                return runner(args, timeout, stdin_text, on_line)
            s.run = run
            s.run_all()
            self.assertEqual(len(calls), 1)
            self.assertIn("ElementLabs.LMStudio", calls[0])

    def test_clean_line_strips_ansi_and_spinners(self):
        from wa.lmstudio_setup import clean_line
        self.assertEqual(clean_line("\x1b[?25l\x1b[0K"), "")
        self.assertEqual(clean_line("\u2833 Resolving download plan...\x1b[0K"), "Resolving download plan...")
        self.assertEqual(clean_line("\u2819 "), "")

    def test_stub_catalog_entry_is_not_a_model(self):
        out = json.dumps([{"type": "llm", "modelKey": "qwen/qwen3-14b", "sizeBytes": 174000}])
        self.assertEqual(parse_models(out), [])

    def test_download_file_resumes(self):
        import http.server
        import threading
        from wa.lmstudio_setup import download_file
        data = bytes(range(256)) * 2000

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                start = int(rng.split("=")[1].rstrip("-")) if rng else 0
                body = data[start:]
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = os.path.join(tmp, "a", "m.gguf")
                os.makedirs(os.path.dirname(dest))
                with open(dest + ".part", "wb") as f:
                    f.write(data[:1000])
                import urllib.request
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                size = download_file("http://127.0.0.1:%d/x" % srv.server_port, dest, opener=op)
                self.assertEqual(size, len(data))
                self.assertEqual(open(dest, "rb").read(), data)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_update_config_keeps_larger_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "c.json")
            json.dump({"llm": {"max_tokens": 4000}}, open(p, "w"))
            d = update_config_file(p, reasoning=True)
            self.assertEqual(d["llm"]["max_tokens"], 4000)


def release(tag, assets=True, draft=False, pre=False):
    return {"tag_name": tag, "draft": draft, "prerelease": pre, "body": "catatan", "html_url": "u",
            "assets": [{"name": updater.ASSET_NAME, "browser_download_url": "http://x/" + tag, "size": 5}] if assets else []}


class UpdaterTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(updater.is_newer("0.10.0", "0.9.5"))
        self.assertFalse(updater.is_newer("0.2.0", "0.2.0"))

    def test_pick_release_ignores_other_tags_and_drafts(self):
        rels = [release("v9.9.9"), release("asistenpc-v0.3.0", draft=True),
                release("asistenpc-v0.2.5", assets=False), release("asistenpc-v0.2.1"),
                release("asistenpc-v0.2.0")]
        self.assertEqual(updater.pick_release(rels)["version"], "0.2.1")

    def test_check_for_update(self):
        rels = [release("asistenpc-v0.3.0")]
        self.assertEqual(updater.check_for_update("0.2.0", fetch=lambda: rels)["version"], "0.3.0")
        self.assertIsNone(updater.check_for_update("0.3.0", fetch=lambda: rels))

    def test_default_repo_and_api_url(self):
        self.assertEqual(updater.REPO, "Mazehax/AI-Hazmat")
        self.assertIn("/repos/Mazehax/AI-Hazmat/releases", updater.api_url())
        from wa.config import load_config
        self.assertEqual(load_config(None)["update"]["repo"], "Mazehax/AI-Hazmat")

    def test_network_error_is_silent(self):
        def boom():
            raise OSError("offline")
        self.assertIsNone(updater.check_for_update("0.1.0", fetch=boom))

    def test_safe_extract_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("../evil.txt", "x")
            with self.assertRaises(ValueError):
                updater.safe_extract(z, os.path.join(tmp, "out"))

    def test_find_payload_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("AsistenPC/AsistenPC.exe", "x")
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            updater.safe_extract(z, out)
            self.assertTrue(updater.find_payload_root(out).endswith("AsistenPC"))

    def test_swap_script_protects_config_and_data(self):
        s = updater.build_swap_script("N", "A", "A\\AsistenPC.exe", 42)
        self.assertIn("/XF config.json", s)
        self.assertIn("/XD data", s)
        self.assertIn("PID eq 42", s)
        self.assertIn("update.log", updater.build_swap_script("N", "A", "E", 1, "C:\\x\\update.log"))
        self.assertIn('start "" /D "A" "A\\AsistenPC.exe"', s)


if __name__ == "__main__":
    unittest.main()
