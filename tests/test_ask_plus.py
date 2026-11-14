"""Cut-off retry, busy fallback, private asks, voting, CLI routes, batch asks, usage, doctor probes, eval grading."""
import json
import subprocess as sp
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ai_neural_network import adapters, health, ledger, llm, registry, throttle
from ai_neural_network.procs import FleetError


def _tiers(env, tiers: dict) -> None:
    path, _ = env
    data = yaml.safe_load(path.read_text())
    data["tiers"] = tiers
    path.write_text(yaml.safe_dump(data))


def _http(port: int) -> dict:
    return {"base_url": f"http://127.0.0.1:{port}/v1", "model": "m", "cost": "free"}


def _reply(text: str, finish: str = "stop") -> dict:
    return {"model": "m", "choices": [{"message": {"content": text}, "finish_reason": finish}]}


def test_cut_off_reply_retries_once_with_bigger_budget(env, monkeypatch):
    _tiers(env, {"t": _http(1)})
    seen = []

    def post(reg, tier, messages, **k):
        seen.append(k["max_tokens"])
        return _reply("", "length") if len(seen) == 1 else _reply("done")

    monkeypatch.setattr(llm, "_post", post)
    r = llm.ask(registry.load(), "t", "hard", max_tokens=4000)
    assert r.text == "done" and seen == [4000, 16384]


def test_busy_provider_falls_to_next_tier_without_cooldown(env, monkeypatch):
    _tiers(env, {"a": _http(1), "b": _http(2)})
    monkeypatch.setattr(llm, "spread_order", lambda reg, ids: ids)

    def post(reg, tier, messages, **k):
        if tier.id == "a":
            raise throttle.Busy("provider 127.0.0.1:1/v1 still busy after 90s")
        return _reply("ok")

    monkeypatch.setattr(llm, "_post", post)
    r = llm.ask(registry.load(), "a,b", "hi")
    assert r.tier == "b" and not health.in_cooldown("a")


def test_private_uses_only_local_tiers(env, monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "STRATA_TEST_LOCK", tmp_path / "no.lock")
    strata = {"base_url": "http://127.0.0.1:47831/v1", "model": "m", "cost": "free"}
    bridge = {"base_url": "http://127.0.0.1:4097/v1", "model": "m", "cost": "free"}  # localhost, but forwards to the cloud
    _tiers(env, {"cloud": {"base_url": "https://example.com/v1", "model": "m", "cost": "free"}, "bridge": bridge, "loc": strata})
    monkeypatch.setattr(llm, "_post", lambda reg, tier, messages, **k: _reply(tier.id))
    assert llm.local_tiers(registry.load()) == ["loc"]
    assert llm.ask(registry.load(), "cloud", "secret", private=True).tier == "loc"
    _tiers(env, {"cloud": {"base_url": "https://example.com/v1", "model": "m", "cost": "free"}, "bridge": bridge})
    with pytest.raises(FleetError, match="no local tier"):
        llm.ask(registry.load(), "cloud", "secret", private=True)

def test_vote_returns_majority_short_answer(env, monkeypatch):
    _tiers(env, {"a": _http(1), "b": _http(2), "c": _http(3)})
    said = {"a": "work...\nANSWER: 42", "b": "ANSWER: **42.**", "c": "ANSWER: 41"}
    monkeypatch.setattr(llm, "_post", lambda reg, tier, messages, **k: _reply(said[tier.id]))
    r = llm.vote(registry.load(), "a,b,c", "6*7?")
    assert r.data["answer"] == "42" and (r.data["agree"], r.data["of"]) == (2, 3)
    assert sorted(r.tier.split(",")) == ["a", "b"]


def test_cli_argv_for_codex_claude_and_agy_images(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    T = registry.Tier
    codex = T("g", "codex://o", "gpt-x", "claude", cli="codex", effort="max")
    argv, stdin, _ = llm._cli_argv(codex, "hi", tmp_path, ["image1.png"])
    assert argv[:3] == ["codex", "exec", "-"] and stdin == "hi"
    assert "model_reasoning_effort=max" in argv and argv[argv.index("-i") + 1] == "image1.png"
    argv, stdin, _ = llm._cli_argv(T("c", "claude://a", "opus-x", "claude", cli="claude", effort="max"), "hi", tmp_path, [])
    assert argv[argv.index("--effort") + 1] == "max" and argv[argv.index("--tools") + 1] == "" and stdin == "hi"
    argv, stdin, text = llm._cli_argv(T("a", "agy://g", "gem", "flat", cli="agy"), "look", tmp_path, ["image1.png"])
    assert stdin is None and "image1.png" in text and "--effort" not in argv


def test_codex_tier_reads_answer_file(env, monkeypatch):
    _tiers(env, {"g": {"base_url": "codex://o", "cli": "codex", "model": "gpt-x", "cost": "claude"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)

    def fake_run(argv, cwd, **kw):
        out = Path(argv[argv.index("-o") + 1])
        out.write_text("ANSWER: 7", encoding="utf-8")
        assert out.parent == Path(cwd) and kw["input"] == "q"
        return sp.CompletedProcess(argv, 0, "tokens used\n1,234\n", "")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    r = llm.ask(registry.load(), "g", "q")
    assert r.text == "ANSWER: 7" and r.usage["prompt_tokens"] == 1234


def test_ask_many_runs_agy_once_for_all_prompts(env, monkeypatch):
    _tiers(env, {"g": {"base_url": "agy://google", "cli": "agy", "model": "gem", "cost": "flat"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    calls = []

    def fake_run(argv, **kw):
        calls.append(kw["input"])
        res = [{"event": "result", "result": {"status": "SUCCESS", "response": f"A{i}"}} for i in (1, 2)]
        return sp.CompletedProcess(argv, 0, "\n".join(json.dumps(r) for r in res), "")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    rows = llm.ask_many(registry.load(), "g", ["one", "two", "three"])
    assert len(calls) == 1 and len(calls[0].splitlines()) == 3
    assert [r.get("text") for r in rows] == ["A1", "A2", None] and "error" in rows[2]
    assert [r["status"] for r in ledger.read()[-3:]] == ["ok", "ok", "error"]


def test_ledger_usage_windows_and_quota_hits():
    at = datetime(2026, 11, 6, 12, tzinfo=UTC)
    rows = [{"ts": (at - timedelta(days=d)).isoformat(), "type": "ask", "tier": "t", "status": s, "error": e}
            for d, s, e in ((0, "ok", ""), (3, "error", "HTTP 429 slow down"), (20, "ok", ""), (40, "ok", ""))]
    u = ledger.usage(rows, at)["ask:t"]
    assert (u["today"], u["7d"], u["30d"], u["failed_30d"], u["quota_30d"]) == (1, 2, 3, 1, 1)


def test_doctor_live_probes_tier_alone(env, monkeypatch):
    from ai_neural_network import doctor

    _tiers(env, {"dead": {**_http(1), "fallback": ["t"]}, "t": _http(2)})

    def post(reg, tier, messages, **k):
        if tier.id == "dead":
            raise llm.ProviderError("tier dead HTTP 404: gone", status=404)
        return _reply("OK")

    monkeypatch.setattr(llm, "_post", post)
    rows = {r["id"]: r for r in doctor.doctor(registry.load(), live=True) if r["kind"] == "tier"}
    assert rows["dead"]["ok"] is False and rows["t"]["ok"] is True


def test_eval_grader_kinds():
    from ai_neural_network.evals import TASKS, grade

    by = {t["id"]: t for t in TASKS}
    assert grade(by["modpow"], "...\nANSWER: 3,978") and not grade(by["modpow"], "ANSWER: 3979")
    assert grade(by["format"], "ANSWER: quick-quiet-queen-quilt") and not grade(by["format"], "ANSWER: quick quiet")
    assert grade(by["logjson"], "```json\n" + by["logjson"]["answer"] + "\n```")
    assert grade(by["logjson"], "work\nANSWER: " + by["logjson"]["answer"])  # JSON on the ANSWER line
    assert grade(by["date"], "ANSWER: 2029-08-02 thursday") and grade(by["puzzle"], "ANSWER: Dee 2")


def test_codex_usage_limit_parks_tier_until_reset(env, monkeypatch):
    _tiers(env, {"g": {"base_url": "codex://o", "cli": "codex", "model": "gpt-x", "cost": "claude"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    msg = ("ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
           "https://chatgpt.com/settings/usage to purchase more credits or try again at Nov 12th, 2099 9:24 AM.")
    err = "x" * 400 + "\n" + msg + "\n" + "y" * 400  # reason sits mid-stderr, as codex prints it
    monkeypatch.setattr(llm.subprocess, "run", lambda argv, **kw: sp.CompletedProcess(argv, 1, "", err))
    with pytest.raises(FleetError, match="usage limit"):
        llm.ask(registry.load(), "g", "q")
    until = health.cooldown_until("g")
    assert until is not None and until.year == 2099  # parked until the reset, not 15 min


def test_agy_empty_reply_gets_one_retry(env, monkeypatch):
    _tiers(env, {"g": {"base_url": "agy://google", "cli": "agy", "model": "gem", "cost": "flat"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    replies = iter(["", "hi"])
    monkeypatch.setattr(llm.subprocess, "run", lambda argv, **kw: sp.CompletedProcess(
        argv, 0, json.dumps({"status": "SUCCESS", "response": next(replies)}), ""))
    assert llm.ask(registry.load(), "g", "q").text == "hi"


def test_daily_cap_moves_to_next_tier_without_spending(env, monkeypatch):
    _tiers(env, {"sub": {**_http(1), "daily_cap": 2, "fallback": ["t"]}, "t": _http(2)})
    monkeypatch.setattr(llm, "_post", lambda reg, tier, messages, **k: _reply(tier.id))
    reg = registry.load()
    assert [llm.ask(reg, "sub", "q").tier for _ in range(3)] == ["sub", "sub", "t"]
    with pytest.raises(llm.CapReached, match="2/2"):
        llm.ask(reg, "sub,", "q")
    assert sum(r["tier"] == "sub" for r in ledger.read()) == 2  # the refused call cost nothing


def test_expired_login_parks_cli_tier_for_an_hour(env, monkeypatch):
    _tiers(env, {"c": {"base_url": "claude://a", "cli": "claude", "model": "opus", "cost": "claude"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    out = json.dumps({"is_error": True, "result": "Failed to authenticate: OAuth session expired"})
    monkeypatch.setattr(llm.subprocess, "run", lambda argv, **kw: sp.CompletedProcess(argv, 1, out, ""))
    with pytest.raises(FleetError, match="log in again"):
        llm.ask(registry.load(), "c", "q")
    until = health.cooldown_until("c")
    assert until is not None and (until - datetime.now(UTC)).total_seconds() > 3000


def test_down_provider_is_skipped_for_rest_of_call(env, monkeypatch):
    _tiers(env, {"n1": {**_http(1), "fallback": ["n2", "o"]}, "n2": _http(1), "o": _http(2)})
    tried = []

    def post(reg, tier, messages, **k):
        tried.append(tier.id)
        if tier.id != "o":
            raise llm.ProviderError(f"tier {tier.id} unreachable", down=True)
        return _reply("ok")

    monkeypatch.setattr(llm, "_post", post)
    assert llm.ask(registry.load(), "n1", "q").tier == "o" and tried == ["n1", "o"]  # n2 shares n1's provider


def test_auto_skips_cli_tiers(env):
    _tiers(env, {"a": {"base_url": "agy://g", "cli": "agy", "model": "g", "cost": "flat"}, "h": _http(1),
                 "f": {**_http(2), "cost": "flat"}})
    assert llm.candidates(registry.load(), "auto") == ["h", "f"]


def test_agy_plain_agent_only_without_images(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    t = registry.Tier("a", "agy://g", "gem", "flat", cli="agy")
    argv, _, _ = llm._cli_argv(t, "q", tmp_path, [])
    assert argv[argv.index("--agent") + 1] == "fleet-ask" and (tmp_path / ".agents/agents/fleet-ask.md").exists()
    argv, _, _ = llm._cli_argv(t, "q", tmp_path, ["image1.png"])
    assert "--agent" not in argv


def test_agy_low_credit_balance_cools_tier(env, monkeypatch):
    _tiers(env, {"o": {"base_url": "agy://google", "cli": "agy", "model": "opus", "cost": "claude"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    out = json.dumps({"status": "ERROR", "error": "Your AI credits balance is too low to continue."})
    monkeypatch.setattr(llm.subprocess, "run", lambda argv, **kw: sp.CompletedProcess(argv, 1, out, ""))
    with pytest.raises(FleetError, match="too low"):
        llm.ask(registry.load(), "o", "q")
    assert health.in_cooldown("o")


def test_agy_long_prompt_goes_on_stdin(env, monkeypatch):
    """Over the argv limit the prompt rides stdin as stream-json, never a file path (agy ask runs with tools off)."""
    _tiers(env, {"a": {"base_url": "agy://g", "cli": "agy", "model": "gem", "cost": "flat"}})
    monkeypatch.setattr(adapters, "_exe", lambda name: name)
    long = "Q " + "x" * (adapters.ARG_PROMPT_LIMIT + 10)
    seen = {}

    def fake_run(argv, cwd, **kw):
        seen["argv"], seen["input"] = argv, kw["input"]
        out = '{"event":"step_update"}\n{"event":"result","result":{"status":"SUCCESS","response":"ok"}}\n'
        return sp.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    r = llm.ask(registry.load(), "a", long)
    assert r.text == "ok"
    assert "--print=" in seen["argv"] and all(long not in a and "prompt.md" not in a for a in seen["argv"])
    assert json.loads(seen["input"]) == {"event": "user", "message": {"content": long}}
    inv = adapters.AgyAdapter().build(None, None, long, Path("p.md"), Path("."), 60)
    assert json.loads(inv.stdin_text)["message"]["content"] == long and "p.md" not in " ".join(inv.argv)
