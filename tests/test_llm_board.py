import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml
from conftest import BD, HELLO_CHECK

from ai_neural_network import health, registry
from ai_neural_network.board import Board
from ai_neural_network.card import TaskCard
from ai_neural_network.llm import ask
from ai_neural_network.procs import FleetError
from ai_neural_network.runner import RunOptions
from ai_neural_network.work import batch, work


class _Stub(BaseHTTPRequestHandler):
    replies: list[str] = []
    seen: list[dict] = []
    bodies: list[dict] = []

    def do_POST(self):  # noqa: N802
        _Stub.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        _Stub.seen.append({k.lower(): v for k, v in self.headers.items()})
        body = json.dumps(
            {
                "model": "stub",
                "choices": [{"message": {"content": _Stub.replies.pop(0)}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Hot(BaseHTTPRequestHandler):
    """Always answers 429 rate-limit with Retry-After: 600, like a provider that got overheated."""

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"error": {"message": "rate limit exceeded, slow down"}}).encode()
        self.send_response(429)
        self.send_header("Retry-After", "600")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub(env):
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {"t": {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "cost": "free"}}
    path.write_text(yaml.safe_dump(data))
    yield
    srv.shutdown()


@pytest.fixture
def hot_and_stub(env):
    hot = HTTPServer(("127.0.0.1", 0), _Hot)
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    for s in (hot, srv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {
        "hot": {"base_url": f"http://127.0.0.1:{hot.server_port}/v1", "model": "m", "cost": "free"},
        "t": {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "cost": "free"},
    }
    path.write_text(yaml.safe_dump(data))
    yield
    hot.shutdown()
    srv.shutdown()


SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}


def test_ask_text(stub):
    _Stub.replies = ["hello"]
    r = ask(registry.load(), "t", "hi")
    assert r.text == "hello" and r.valid is None


@pytest.fixture
def stub_session(env):
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {
        "t": {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "cost": "free", "session_header": True}
    }
    path.write_text(yaml.safe_dump(data))
    yield
    srv.shutdown()


def test_ask_sends_session_header_when_tier_requires_it(stub_session):
    _Stub.replies = ["hello"]
    _Stub.seen = []
    reg = registry.load()
    assert reg.tiers["t"].session_header is True
    r = ask(reg, "t", "hi")
    assert r.text == "hello" and len(_Stub.seen) == 1
    sid = _Stub.seen[0].get("x-opencode-session")
    assert sid, _Stub.seen[0]
    uuid.UUID(sid)  # a fresh UUID per request, like the gate used to send


def test_ask_omits_session_header_by_default(stub):
    _Stub.replies = ["hello"]
    _Stub.seen = []
    assert registry.load().tiers["t"].session_header is False
    r = ask(registry.load(), "t", "hi")
    assert r.text == "hello"
    assert all("x-opencode-session" not in h for h in _Stub.seen)


def test_ask_schema_repair(stub):
    _Stub.replies = ['```json\n{"n": "x"}\n```', '{"n": 3}']
    r = ask(registry.load(), "t", "give n", schema=SCHEMA)
    assert r.valid is True and r.data == {"n": 3}


def test_ask_falls_back_and_cools_down(hot_and_stub):
    _Stub.replies = ["hello"]
    r = ask(registry.load(), "hot,t", "hi")
    assert r.text == "hello" and r.tier == "t"  # hot 429ed -> next candidate served
    assert health.in_cooldown("hot")  # and hot was cooled down, not retried blindly


def test_ask_skips_cooled_down_tier(hot_and_stub):
    _Stub.replies = ["one", "two"]
    ask(registry.load(), "hot,t", "hi")  # hot 429s -> cooldown
    r = ask(registry.load(), "hot,t", "hi")
    assert r.tier == "t" and r.text == "two"  # hot skipped up front, no wasted request


def test_ask_single_overheated_tier_raises(hot_and_stub):
    with pytest.raises(FleetError, match="HTTP 429"):
        ask(registry.load(), "hot", "hi")


def test_ask_unreachable_tier_falls_back(env, tmp_path):
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {"dead": {"base_url": "http://127.0.0.1:59999/v1", "model": "m", "cost": "free"}}
    path.write_text(yaml.safe_dump(data))
    reg = registry.load()
    with pytest.raises(FleetError, match="unreachable"):
        ask(reg, "dead", "hi", timeout_s=5)


def test_ask_skips_strata_tiers_during_timed_test(stub, env, tmp_path, monkeypatch):
    from ai_neural_network import llm

    lock = tmp_path / "strata-testing.lock"
    lock.write_text("test", encoding="utf-8")
    # dead port stands in for Strata, so a broken filter fails as "unreachable", never hits the real engine
    monkeypatch.setattr(llm, "STRATA_TEST_LOCK", lock)
    monkeypatch.setattr(llm, "STRATA_PORT", ":59998/")
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"]["strata"] = {"base_url": "http://127.0.0.1:59998/v1", "model": "m", "cost": "free"}
    path.write_text(yaml.safe_dump(data))
    _Stub.replies = ["cloud"]
    assert ask(registry.load(), "strata,t", "hi").tier == "t"
    with pytest.raises(FleetError, match="timed test"):
        ask(registry.load(), "strata", "hi", timeout_s=5)
    lock.unlink()
    with pytest.raises(FleetError, match="unreachable"):
        ask(registry.load(), "strata", "hi", timeout_s=5)


def test_batch_parallel(env, tmp_path):
    cards = []
    for i in range(3):
        d = tmp_path / f"w{i}"
        d.mkdir()
        cards.append(TaskCard(goal="hello", cwd=str(d), done_when=[HELLO_CHECK]))
    envs = list(batch(registry.load(), cards, parallel=2, worker="fake-ok", opts=RunOptions(gpu_busy=False)))
    assert len(envs) == 3 and all(e.ok for e in envs)


needs_bd = pytest.mark.skipif(not BD.exists(), reason="beads not installed")


@needs_bd
def test_board_claim_and_work_order(env, tmp_path):
    reg = registry.load()
    b = Board(reg)
    w1, w2 = tmp_path / "a", tmp_path / "b"
    w1.mkdir()
    w2.mkdir()
    first = b.add(TaskCard(goal="first", cwd=str(w1), worker="fake-ok", done_when=[HELLO_CHECK]), priority=1)
    second = b.add(TaskCard(goal="second", cwd=str(w2), worker="fake-ok", done_when=[HELLO_CHECK]), deps=[first])
    assert [i["id"] for i in b.ready()] == [first]
    assert b.claim(first, "x1") and not b.claim(first, "x2")
    b.reopen(first, "x1", "test reopen")
    rows = work(reg, b, max_tasks=2, actor="tester", opts=RunOptions(gpu_busy=False))
    assert [r["task"] for r in rows] == [first, second] and all(r["status"] == "ok" for r in rows)
    assert b.ready() == []


@needs_bd
def test_work_failure_reopens_with_label(env, tmp_path):
    reg = registry.load()
    b = Board(reg)
    d = tmp_path / "c"
    d.mkdir()
    tid = b.add(TaskCard(goal="lie", cwd=str(d), worker="fake-liar", done_when=[HELLO_CHECK]))
    rows = work(reg, b, max_tasks=1, actor="tester", opts=RunOptions(gpu_busy=False))
    assert rows[0]["status"] == "failed" and rows[0]["label"] == "fleet:failed-1"
    issue = b.get(tid)
    assert issue["status"] == "open" and "fleet:failed-1" in (issue.get("labels") or [])


class _DropsFirst(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        _DropsFirst.calls += 1
        if _DropsFirst.calls == 1:
            self.close_connection = True
            return
        body = json.dumps({"model": "stub", "choices": [{"message": {"content": "ok"}}], "usage": {}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_dropped_connection_gets_one_retry(env):
    srv = HTTPServer(("127.0.0.1", 0), _DropsFirst)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {"t": {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "cost": "free"}}
    path.write_text(yaml.safe_dump(data))
    try:
        assert ask(registry.load(), "t", "hi").text == "ok"
        assert _DropsFirst.calls == 2
    finally:
        srv.shutdown()


def test_only_dropped_connections_retry():
    import urllib.error

    from ai_neural_network.llm import _dropped

    assert _dropped(ConnectionResetError())
    assert _dropped(urllib.error.URLError(ConnectionResetError()))
    assert not _dropped(urllib.error.URLError(ConnectionRefusedError()))
    assert not _dropped(TimeoutError())


def test_ledger_records_error_reason(env):
    from ai_neural_network import ledger

    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {"dead": {"base_url": "http://127.0.0.1:59999/v1", "model": "m", "cost": "free"}}
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(FleetError):
        ask(registry.load(), "dead", "hi", timeout_s=5)
    row = ledger.read()[-1]
    assert row["status"] == "error" and "unreachable" in row["error"]


def test_doctor_live_gives_reasoning_room(stub, monkeypatch):
    from ai_neural_network import doctor, llm

    seen = []
    real = llm._post
    monkeypatch.setattr(llm, "_post", lambda *a, **k: seen.append(k["max_tokens"]) or real(*a, **k))
    _Stub.replies = ["OK"]
    rows = [r for r in doctor.doctor(registry.load(), live=True) if r["kind"] == "tier"]
    assert rows[0]["ok"] and seen[0] >= 256  # 5 tokens left every reasoning tier empty


def test_auto_never_picks_paid_tiers(env):
    from ai_neural_network.llm import candidates

    path, _ = env
    data = yaml.safe_load(path.read_text())
    costs = ("metered", "claude", "flat", "free")
    data["tiers"] = {c: {"base_url": "http://127.0.0.1:1/v1", "model": "m", "cost": c} for c in costs}
    path.write_text(yaml.safe_dump(data))
    reg = registry.load()
    assert candidates(reg, "auto") == ["free", "flat"]
    assert candidates(reg, "metered") == ["metered"]  # still usable when named


def test_secrets_file_beats_stale_env(env, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ai_neural_network.llm import _tier_key

    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / ".env").write_text("K_A=fresh\nK_EMPTY=\n", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("K_A", "stale")
    monkeypatch.setenv("K_EMPTY", "fromenv")
    assert _tier_key(SimpleNamespace(key_env="K_A", id="t")) == "fresh"
    assert _tier_key(SimpleNamespace(key_env="K_EMPTY", id="t")) == "fromenv"  # empty line falls back to env


def test_catalog_notes_flags_gone_and_expiring(env):
    from ai_neural_network.doctor import CATALOG_URL, catalog_notes

    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {
        "gone": {"base_url": "https://x/v1", "model": "old", "cost": "free", "catalog": "p"},
        "nojson": {"base_url": "https://x/v1", "model": "m2", "cost": "free", "catalog": "p"},
        "fine": {"base_url": "https://x/v1", "model": "m1", "cost": "free", "catalog": "p"},
        "or": {"base_url": "https://openrouter.ai/api/v1", "model": "a:free", "cost": "free"},
    }
    path.write_text(yaml.safe_dump(data))
    cat = {"p": {"models": {"m1": {"structured_output": True}, "m2": {"structured_output": False}}}}
    orm = {"data": [{"id": "a:free", "expiration_date": "2026-12-01"}]}
    notes = catalog_notes(registry.load(), fetch=lambda u: cat if u == CATALOG_URL else orm)
    assert "not in models.dev" in notes["gone"] and "structured output" in notes["nojson"]
    assert "fine" not in notes and "2026-12-01" in notes["or"]


def test_spent_free_quota_parks_every_free_sibling(env, monkeypatch):
    from ai_neural_network import llm

    hot = HTTPServer(("127.0.0.1", 0), _Hot)
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    for s in (hot, srv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    path, _ = env
    data = yaml.safe_load(path.read_text())
    hb = f"http://127.0.0.1:{hot.server_port}/v1"
    data["tiers"] = {
        "f1": {"base_url": hb, "model": "a:free", "cost": "free"},
        "f2": {"base_url": hb, "model": "b:free", "cost": "free"},
        "t": {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "cost": "free"},
    }
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(llm, "_free_daily_reset_s", lambda tier: 3600)
    monkeypatch.setattr(llm, "spread_order", lambda reg, ids: ids)
    _Stub.replies = ["hello"]
    try:
        r = ask(registry.load(), "f1,f2,t", "hi")
        assert r.tier == "t" and health.in_cooldown("f1") and health.in_cooldown("f2")
        assert "daily quota" in health.load()["f2"]["reason"]
    finally:
        hot.shutdown()
        srv.shutdown()


def test_free_daily_reset_parses_key_endpoint(monkeypatch):
    import io

    from ai_neural_network import llm
    from ai_neural_network.registry import Tier

    tier = Tier(id="t", base_url="https://openrouter.ai/api/v1", model="a:free", cost="free", key_env="K")
    monkeypatch.setattr(llm, "_tier_key", lambda t: "k")
    for left, expect_none in ((0, False), (7, True)):
        body = json.dumps({"data": {"free_model_daily_requests": {"remaining": left}}}).encode()
        monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout, b=body: io.BytesIO(b))
        s = llm._free_daily_reset_s(tier)
        assert (s is None) == expect_none and (s is None or 60 < s <= 86460)
    assert llm._free_daily_reset_s(Tier(id="x", base_url="https://openrouter.ai/api/v1", model="paid", cost="free")) is None


def test_cli_tier_answers_through_agy(env, monkeypatch):
    import subprocess as sp

    from ai_neural_network import adapters, llm

    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {
        "g": {"base_url": "agy://google", "cli": "agy", "model": "gemini-x", "cost": "flat"},
        "t": {"base_url": "http://127.0.0.1:1/v1", "model": "m", "cost": "free"},
    }
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    seen, out = [], {}

    def fake_run(argv, **kw):
        seen.append(argv)
        return sp.CompletedProcess(argv, 0, out["stdout"], "")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    out["stdout"] = json.dumps({"status": "SUCCESS", "response": '{"n": 4}\n', "usage": {"input_tokens": 9, "output_tokens": 2}})
    r = ask(registry.load(), "g", "2+2", schema=SCHEMA)
    assert r.valid is True and r.data == {"n": 4} and r.usage["prompt_tokens"] == 9
    assert seen[0][:2] == ["agy", "-p"] and seen[0][seen[0].index("--model") + 1] == "gemini-x"
    out["stdout"] = json.dumps({"status": "ERROR", "error": "You exceeded your usage limit"})
    with pytest.raises(FleetError, match="usage limit"):
        ask(registry.load(), "g", "hi")
    assert health.in_cooldown("g")  # quota text from agy cools the tier like a 429


def test_empty_answer_falls_back_to_next_tier(stub, env, monkeypatch):
    from ai_neural_network import ledger, llm

    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"]["t2"] = dict(data["tiers"]["t"])
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(llm, "spread_order", lambda reg, ids: ids)
    _Stub.replies = ["", "hello"]
    r = ask(registry.load(), "t,t2", "hi")
    assert r.tier == "t2" and r.text == "hello"
    assert [row["status"] for row in ledger.read()[-2:]] == ["error", "ok"]


def _with_tiers(env, tiers: dict) -> None:
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = {**data["tiers"], **tiers}
    path.write_text(yaml.safe_dump(data))


def test_named_tier_falls_back_in_order_and_parks_dead_one(stub, env):
    _with_tiers(env, {"dead": {"base_url": "http://127.0.0.1:59999/v1", "model": "m", "cost": "free", "fallback": ["t"]}})
    _Stub.replies = ["hello"]
    r = ask(registry.load(), "dead", "hi", timeout_s=5)
    assert r.tier == "t" and r.text == "hello"
    assert health.in_cooldown("dead")  # next ask skips it instead of re-waiting the timeout


def test_images_go_as_image_url_parts_and_skip_cli_tiers(stub, env, tmp_path):
    _with_tiers(env, {"c": {"base_url": "claude://a", "model": "g", "cost": "claude", "cli": "claude", "fallback": ["t"]}})
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG fake")
    _Stub.replies, _Stub.bodies = ["blue"], []
    r = ask(registry.load(), "c", "colour?", images=[str(img)])
    assert r.tier == "t"  # claude CLI runs with no tools: skipped, no subprocess
    parts = _Stub.bodies[0]["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "colour?"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    with pytest.raises(FleetError, match="not an image"):
        ask(registry.load(), "t", "x", images=[str(tmp_path)])


def test_registry_rejects_unknown_fallback(env):
    _with_tiers(env, {"x": {"base_url": "http://h/v1", "model": "m", "cost": "free", "fallback": ["nope"]}})
    with pytest.raises(ValueError, match="fallback"):
        registry.load()
