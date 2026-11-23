import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wa.config import load_config
from wa.core import Agent, Observation, UI, match_element, route_goal, validate_action
from wa.llm import LLMError, LocalLLM, extract_json
from wa.memory import Memory
from wa.safety import Safety


def obs(title, *elements):
    return Observation(window_title=title,
                       elements=[{"id": i, "name": n, "ctype": t} for i, (n, t) in enumerate(elements, 1)])


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, image_b64=None, **kw):
        self.calls += 1
        if not self.replies:
            raise AssertionError("LLM dipanggil lebih banyak dari yang diharapkan")
        return self.replies.pop(0)


class FakeEnv:
    def __init__(self, screens):
        self.screens = screens
        self.i = 0
        self.performed = []

    def observe(self):
        return self.screens[min(self.i, len(self.screens) - 1)]

    def perform(self, action):
        self.performed.append(dict(action))
        self.i += 1
        return True, "ok"


class ScriptedUI(UI):
    def __init__(self, confirms=(), answers=()):
        self.confirms = list(confirms)
        self.answers = list(answers)
        self.logs = []
        self.confirm_msgs = []

    def log(self, msg):
        self.logs.append(msg)

    def confirm(self, msg):
        self.confirm_msgs.append(msg)
        return self.confirms.pop(0) if self.confirms else False

    def ask(self, q):
        return self.answers.pop(0) if self.answers else None


def make_agent(llm, env, ui, tmp, **agent_cfg):
    cfg = load_config(None)
    cfg["agent"].update({"step_delay": 0, "auto_reflect": False})
    cfg["agent"].update(agent_cfg)
    return Agent(llm, env, Memory(tmp), Safety(cfg["safety"]), ui, cfg), cfg


def J(**kw):
    return json.dumps(kw)


class FakeSkills:
    def __init__(self, risky=False):
        self.ran, self.risky = [], risky

    def assess(self, action):
        return (self.risky, "uji")

    def run(self, name, args):
        self.ran.append((name, args))
        return "Laporan dibuat: C:\\x.xlsx"


class TestSkillsInAgent(unittest.TestCase):
    def test_model_param_aliases_are_accepted(self):
        o = obs("Excel")
        a = {"action": "focus_window", "window": "Book2 - Excel"}
        self.assertIsNone(validate_action(a, o))
        self.assertEqual(a["title"], "Book2 - Excel")
        b = {"action": "open_app", "app": "excel"}
        self.assertIsNone(validate_action(b, o))
        self.assertEqual(b["name"], "excel")
        self.assertIsNotNone(validate_action({"action": "focus_window"}, o))

    def test_route_goal(self):
        r = route_goal("buka excel dan buatkan tabel laporan keuangan dengan 100 baris data dummy")
        self.assertEqual(r["args"], {"kind": "transaksi", "rows": 100})
        self.assertEqual(route_goal("buatkan laporan penjualan di excel")["args"], {"kind": "penjualan"})
        self.assertTrue(route_goal("pada excel yang saya buka buatkan laporan keuangan bulanan")["args"]["active"])
        self.assertIsNone(route_goal("buka excel"))
        self.assertIsNone(route_goal("tutup excel dan hapus datanya"))
        self.assertIsNone(route_goal("tulis hello di notepad"))

    def test_shortcut_runs_skill_without_llm(self):
        with tempfile.TemporaryDirectory() as d:
            sk = FakeSkills()
            cfg = load_config(None)
            cfg["agent"].update({"step_delay": 0, "auto_reflect": False})
            llm = FakeLLM([])  # tidak boleh dipanggil
            agent = Agent(llm, FakeEnv([obs("x")]), Memory(d), Safety(cfg["safety"]), ScriptedUI(), cfg, skills=sk)
            r = agent.run("buka excel dan buatkan tabel laporan keuangan dengan 100 baris data dummy")
            self.assertEqual(r.status, "done")
            self.assertEqual(sk.ran[0][1], {"kind": "transaksi", "rows": 100})
            self.assertEqual(llm.calls, 0)

    def test_skill_validation(self):
        o = obs("x")
        self.assertIsNone(validate_action({"action": "skill", "name": "make_excel_report", "args": '{"kind": "keuangan"}'}, o))
        self.assertIn("tidak ada", validate_action({"action": "skill", "name": "hack"}, o))

    def test_skill_runs_and_finishes_without_clicking(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Book1 - Excel", ("Cell A1", "Edit"))])
            llm = FakeLLM([J(action="skill", name="make_excel_report", args={"kind": "keuangan", "active": True}, risk="low"),
                           J(action="done", summary="laporan siap")])
            sk = FakeSkills()
            cfg = load_config(None)
            cfg["agent"].update({"step_delay": 0, "auto_reflect": False})
            agent = Agent(llm, env, Memory(d), Safety(cfg["safety"]), ScriptedUI(), cfg, skills=sk)
            r = agent.run("buat laporan keuangan")
            self.assertEqual(r.status, "done")
            self.assertEqual(sk.ran, [("make_excel_report", {"kind": "keuangan", "active": True})])
            self.assertEqual(env.performed, [])  # tidak ada klik/ketik

    def test_risky_skill_needs_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("x")])
            llm = FakeLLM([J(action="skill", name="recycle", args={"path": "a"}, risk="low"),
                           J(action="fail", reason="ditolak")])
            sk = FakeSkills(risky=True)
            ui = ScriptedUI()
            cfg = load_config(None)
            cfg["agent"].update({"step_delay": 0, "auto_reflect": False})
            agent = Agent(llm, env, Memory(d), Safety(cfg["safety"]), ui, cfg, skills=sk)
            agent.run("hapus a")
            self.assertEqual(sk.ran, [])  # ditolak (ScriptedUI default False)
            self.assertEqual(len(ui.confirm_msgs), 1)


class TestJson(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```'), {"a": {"b": "}"}})
        self.assertEqual(extract_json('<think>hmm {x}</think>{"a": 2} trailing'), {"a": 2})
        self.assertIsNone(extract_json("no json here"))
        self.assertIsNone(extract_json('{"a": '))


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.s = Safety({})

    def test_click(self):
        self.assertTrue(self.s.assess({"action": "click"}, {"name": "Delete all"}, "Files")[0])
        self.assertTrue(self.s.assess({"action": "click"}, {"name": "Kirim"}, "Mail")[0])
        self.assertFalse(self.s.assess({"action": "click"}, {"name": "Save"}, "Notepad")[0])
        self.assertTrue(self.s.assess({"action": "click"}, {"name": "Bayar sekarang"}, "Toko")[0])

    def test_other(self):
        self.assertTrue(self.s.assess({"action": "hotkey", "keys": ["Alt", "F4"]})[0])
        self.assertTrue(self.s.assess({"action": "press", "key": "Delete"})[0])
        self.assertTrue(self.s.assess({"action": "type", "text": "hi", "enter": True}, None, "WhatsApp")[0])
        self.assertFalse(self.s.assess({"action": "type", "text": "hi", "enter": True}, None, "Notepad")[0])
        self.assertTrue(self.s.assess({"action": "type", "text": "x"}, {"name": "Password"}, "Login")[0])
        self.assertTrue(self.s.assess({"action": "click", "risk": "high"}, {"name": "OK"}, "x")[0])
        self.assertTrue(self.s.assess({"action": "click"}, {"name": "OK"}, "Internet Banking BCA - Chrome")[0])

    def test_confirm_all(self):
        self.assertTrue(Safety({"confirm_every_action": True}).assess({"action": "wait"})[0])


class TestMemory(unittest.TestCase):
    def test_lessons(self):
        with tempfile.TemporaryDirectory() as d:
            m = Memory(d)
            self.assertTrue(m.add_lesson("Di Excel, menu Save As ada di File lalu Save a Copy", app="Excel"))
            self.assertFalse(m.add_lesson("di excel menu save as ada di file lalu save a copy!", app="Excel"))
            self.assertEqual(m.lessons[0]["count"], 2)
            self.assertFalse(m.add_lesson("password saya adalah rahasia123"))
            self.assertFalse(m.add_lesson("nomor 123456789012 dipakai"))
            self.assertFalse(m.add_lesson("pendek"))
            m.add_lesson("Notepad menyimpan dengan Ctrl+S", app="Notepad")
            found = m.find_lessons("simpan file excel sebagai", "Book1 - Excel")
            self.assertTrue(found and "Excel" in found[0]["text"])
            self.assertEqual(Memory(d).lessons[0]["text"][:5], "Di Ex")  # persisten

    def test_skills_and_facts(self):
        with tempfile.TemporaryDirectory() as d:
            m = Memory(d)
            self.assertTrue(m.save_skill("Catat", "buka notepad dan tulis catatan harian", [{"action": "wait"}]))
            skill, score = m.find_skill("buka notepad dan tulis catatan harian")
            self.assertEqual(skill["name"], "Catat")
            self.assertAlmostEqual(score, 1.0)
            self.assertLess(m.find_skill("kirim email ke klien")[1], 0.3)
            m.record_run("catat", True)
            self.assertEqual(m.skills[0]["ok_runs"], 1)
            self.assertTrue(m.set_fact("folder_kerja", "D:\\Kerja"))
            self.assertFalse(m.set_fact("pin_atm", "1234"))
            self.assertIn("folder_kerja", m.facts_text())
            self.assertTrue(m.delete_skill("CATAT"))


class TestHelpers(unittest.TestCase):
    def test_match_and_validate(self):
        o = obs("Notepad", ("Save", "Button"), ("File name", "Edit"))
        self.assertEqual(match_element(o.elements, "save", "Button"), 1)
        self.assertEqual(match_element(o.elements, "Savee", "Button"), 1)
        self.assertIsNone(match_element(o.elements, "Print", "Button"))
        self.assertIsNone(validate_action({"action": "click", "id": "2"}, o))
        self.assertIsNotNone(validate_action({"action": "click", "id": 9}, o))
        self.assertIsNotNone(validate_action({"action": "fly"}, o))
        self.assertIsNotNone(validate_action({"action": "hotkey", "keys": "ctrl"}, o))


class TestAgent(unittest.TestCase):
    def test_success_and_recording(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Notepad", ("File name", "Edit"), ("Save", "Button")), obs("Notepad", ("Save", "Button"))])
            llm = FakeLLM([
                J(thought="isi", action="type", id=1, text="halo", enter=False),
                J(thought="simpan", action="click", id=1),
                J(action="done", summary="selesai"),
            ])
            agent, _ = make_agent(llm, env, ScriptedUI(), d)
            res = agent.run("tulis halo di notepad")
            self.assertEqual(res.status, "done")
            self.assertEqual([a["action"] for a in env.performed], ["type", "click"])
            self.assertEqual(res.executed[0]["target"], {"name": "File name", "ctype": "Edit"})
            self.assertNotIn("id", res.executed[0])

    def test_risky_refused(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Files", ("Delete", "Button"))])
            llm = FakeLLM([J(action="click", id=1), J(action="fail", reason="ditolak")])
            ui = ScriptedUI(confirms=[False])
            agent, _ = make_agent(llm, env, ui, d)
            res = agent.run("hapus file")
            self.assertEqual(env.performed, [])
            self.assertEqual(res.status, "failed")
            self.assertEqual(len(ui.confirm_msgs), 1)

    def test_risky_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Files", ("Delete", "Button"))])
            llm = FakeLLM([J(action="click", id=1), J(action="done", summary="ok")])
            agent, _ = make_agent(llm, env, ScriptedUI(confirms=[True]), d)
            self.assertEqual(agent.run("hapus file").status, "done")
            self.assertEqual(len(env.performed), 1)

    def test_invalid_replies(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("A", ("x", "Button"))])
            llm = FakeLLM(["bukan json", J(action="click", id=99), J(action="done", summary="ok")])
            agent, _ = make_agent(llm, env, ScriptedUI(), d)
            self.assertEqual(agent.run("tes").status, "done")
            llm2 = FakeLLM(["x", "x", "x", "x"])
            agent2, _ = make_agent(llm2, env, ScriptedUI(), d)
            self.assertEqual(agent2.run("tes").status, "failed")

    def test_loop_guard(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("A", ("x", "Button"))])
            env.perform = lambda a: (True, "ok")
            llm = FakeLLM([J(action="click", id=1)] * 6)
            agent, _ = make_agent(llm, env, ScriptedUI(), d)
            res = agent.run("tes")
            self.assertEqual(res.status, "failed")
            self.assertIn("mengulang", res.summary)

    def test_stop_and_max_steps(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("A", ("x", "Button"))])
            ui = UI()
            ui.should_stop = lambda: True
            agent, _ = make_agent(FakeLLM([]), env, ui, d)
            self.assertEqual(agent.run("tes").status, "stopped")
            llm = FakeLLM([J(action="wait", seconds=i) for i in range(1, 4)])
            agent, _ = make_agent(llm, env, ScriptedUI(), d, max_steps=3)
            self.assertEqual(agent.run("tes").status, "max_steps")

    def test_ask_user(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("A", ("x", "Button"))])
            llm = FakeLLM([J(action="ask_user", question="nama file?"), J(action="done", summary="ok")])
            agent, _ = make_agent(llm, env, ScriptedUI(answers=["laporan"]), d)
            self.assertEqual(agent.run("tes").status, "done")
            self.assertEqual(agent.llm.calls, 2)

    def test_reflection_learns(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Book1 - Excel", ("Save", "Button"))])
            llm = FakeLLM([
                J(action="click", id=1),
                J(action="wait", seconds=0),
                J(action="press", key="enter"),
                J(action="done", summary="ok"),
                json.dumps({"lessons": ["Di Excel tombol Save ada di pojok kiri atas", "pendek"]}),
            ])
            agent, _ = make_agent(llm, env, ScriptedUI(), d, auto_reflect=True)
            self.assertEqual(agent.run("simpan excel").status, "done")
            self.assertEqual(len(agent.memory.lessons), 1)
            self.assertEqual(agent.memory.lessons[0]["app"], "Excel")

    def test_short_task_skips_reflection(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Book1 - Excel", ("Save", "Button"))])
            llm = FakeLLM([J(action="click", id=1), J(action="done", summary="ok")])
            agent, _ = make_agent(llm, env, ScriptedUI(), d, auto_reflect=True)
            self.assertEqual(agent.run("simpan excel").status, "done")
            self.assertEqual(llm.calls, 2)  # tanpa panggilan refleksi

    def test_skill_replay_without_llm(self):
        with tempfile.TemporaryDirectory() as d:
            screens = [obs("Notepad", ("File name", "Edit"), ("Save", "Button")), obs("Notepad", ("Save", "Button"))]
            env = FakeEnv(screens)
            llm = FakeLLM([])  # tidak boleh dipanggil
            agent, _ = make_agent(llm, env, ScriptedUI(confirms=[True]), d)
            agent.memory.save_skill("Catat", "tulis halo di notepad", [
                {"action": "type", "text": "halo", "enter": False, "target": {"name": "File name", "ctype": "Edit"}},
                {"action": "click", "target": {"name": "Save", "ctype": "Button"}},
            ])
            res = agent.run("tulis halo di notepad")
            self.assertEqual(res.status, "done")
            self.assertEqual(llm.calls, 0)
            self.assertEqual([a["id"] for a in env.performed], [1, 1])
            self.assertEqual(agent.memory.skills[0]["ok_runs"], 1)

    def test_skill_replay_falls_back_to_ai(self):
        with tempfile.TemporaryDirectory() as d:
            env = FakeEnv([obs("Notepad", ("Menu", "Button"))])
            llm = FakeLLM([J(action="click", id=1), J(action="done", summary="ok lewat AI")])
            agent, _ = make_agent(llm, env, ScriptedUI(confirms=[True]), d)
            agent.memory.save_skill("Catat", "tulis halo di notepad", [
                {"action": "click", "target": {"name": "Tombol Hilang", "ctype": "Button"}}])
            res = agent.run("tulis halo di notepad")
            self.assertEqual(res.status, "done")
            self.assertEqual(llm.calls, 2)
            self.assertEqual(agent.memory.skills[0]["ok_runs"], 0)


class _Handler(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen = body
        if body.get("model") == "boom":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"gagal")
            return
        out = {"choices": [{"message": {"content": "<think>x</think>{\"action\": \"done\"}"}}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "model-a"}]}).encode())


class TestLLMClient(unittest.TestCase):
    def test_http(self):
        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cfg = load_config(None)["llm"]
            cfg["base_url"] = "http://127.0.0.1:%d/v1" % srv.server_port
            llm = LocalLLM(cfg)
            self.assertEqual(extract_json(llm.chat([{"role": "user", "content": "hi"}])), {"action": "done"})
            self.assertNotIn("model", _Handler.seen)
            cfg["use_vision"] = True
            llm.chat([{"role": "user", "content": "hi"}], image_b64="AAAA")
            content = _Handler.seen["messages"][-1]["content"]
            self.assertEqual(content[1]["type"], "image_url")
            ok, info = llm.ping()
            self.assertTrue(ok)
            self.assertIn("model-a", info)
            cfg["model"] = "boom"
            with self.assertRaises(LLMError):
                llm.chat([{"role": "user", "content": "hi"}])
        finally:
            srv.shutdown()
        cfg["base_url"] = "http://127.0.0.1:1/v1"
        with self.assertRaises(LLMError):
            LocalLLM(cfg).chat([{"role": "user", "content": "hi"}])
        self.assertFalse(LocalLLM(cfg).ping()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
