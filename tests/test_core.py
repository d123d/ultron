import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import HELLO_CHECK

from ai_neural_network import doctor, health, ledger, registry, router, worktree
from ai_neural_network.adapters import AcpxAdapter, AgyAdapter
from ai_neural_network.card import TaskCard, load_card, render_prompt
from ai_neural_network.cli import main
from ai_neural_network.envelope import parse_fleet_result
from ai_neural_network.procs import ProcResult
from ai_neural_network.runner import RunOptions, run_card


def test_package_registry_loads():
    reg = registry.load(Path(__file__).parents[1] / "src" / "ai_neural_network" / "workers.yaml")
    assert {"opencode", "codex", "agy", "pi", "claude"} <= set(reg.workers)
    assert not ({"crush", "openclaw"} & set(reg.workers))
    assert reg.workers["claude"].opt_in and reg.workers["pi"].resources == []
    assert reg.tiers["utility"].key_env == "OLLAMA_API_KEY"
    assert reg.tiers["utility"].model == "gpt-oss:120b"
    assert reg.tiers["main"].base_url


def test_tcp_probe_defaults_port_by_scheme(monkeypatch):
    seen = []

    def fake_conn(addr, timeout=None):
        seen.append(addr)
        raise OSError("nope")

    monkeypatch.setattr("ai_neural_network.doctor.socket.create_connection", fake_conn)
    assert doctor._tcp("http://example.com/x") is False
    assert doctor._tcp("https://example.com/x") is False
    assert doctor._tcp("https://example.com:8443/x") is False
    assert [port for _, port in seen] == [80, 443, 8443]


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("workers:\n  x: {kind: agent, control: command, cost: free, capabilities: [code], bogus: 1}\n")
    with pytest.raises(ValueError, match="bogus"):
        registry.load(p)


def test_render_prompt_contract(tmp_path):
    card = TaskCard(
        goal="do it",
        cwd=str(tmp_path),
        paths=["a.py", "b.py"],
        done_when=["pytest -q"],
        constraints=["no deps"],
        context_files=["spec.md"],
    )
    text = render_prompt(card, None)
    assert "operated headlessly" in text and "touch only: a.py, b.py" in text
    assert "DONE WHEN these commands succeed:\n- pytest -q" in text
    assert "CONSTRAINTS:\n- no deps" in text and "FLEET_RESULT:" in text


def test_load_card_text_and_yaml(tmp_path):
    assert load_card("just text").goal == "just text"
    f = tmp_path / "c.yaml"
    f.write_text("goal: g\ndone_when: [x]\n")
    c = load_card(f)
    assert c.goal == "g" and c.done_when == ["x"]


def test_parse_fleet_result():
    text = 'FLEET_RESULT: {"status":"partial"}\nmore\n`FLEET_RESULT: {"status": "done", "summary": "s"}`'
    assert parse_fleet_result(text)["status"] == "done"
    assert parse_fleet_result("FLEET_RESULT: not json") is None
    assert parse_fleet_result("") is None


ACPX_LINES = "\n".join(
    [
        '{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"ses_x","update":{"sessionUpdate":"agent_message_chunk",'
        '"messageId":"m1","content":{"type":"text","text":"OK"}}}}',
        '{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"ses_x","update":{"sessionUpdate":"usage_update",'
        '"used":11725,"size":131072,"cost":{"amount":0,"currency":"USD"}}}}',
        '{"jsonrpc":"2.0","id":2,"result":{"stopReason":"end_turn","usage":{"inputTokens":11725,"outputTokens":1}}}',
    ]
)


def test_acpx_parse():
    r = AcpxAdapter().parse(ProcResult(0, ACPX_LINES, "", False, 1.0))
    assert r.reply == "OK" and r.session_id == "ses_x" and r.error is None
    assert r.usage == {"cost_usd": 0, "input_tokens": 11725, "output_tokens": 1}
    err = '{"jsonrpc":"2.0","id":null,"error":{"code":-32603,"message":"Failed to spawn agent command"}}'
    assert "Failed to spawn" in AcpxAdapter().parse(ProcResult(1, err, "", False, 1)).error


def test_agy_parse():
    out = json.dumps(
        {"conversation_id": "c1", "status": "SUCCESS", "response": "OK\n", "usage": {"input_tokens": 5, "output_tokens": 2}}
    )
    r = AgyAdapter().parse(ProcResult(0, out, "", False, 1))
    assert r.reply == "OK" and r.session_id == "c1" and r.error is None and r.usage["output_tokens"] == 2


def run(goal_cwd, worker, **kw):
    card = TaskCard(goal="write hello.txt", cwd=str(goal_cwd), done_when=kw.pop("done_when", [HELLO_CHECK]))
    return run_card(registry.load(), card, worker, RunOptions(gpu_busy=False, **kw))


def test_run_ok_verified(env, repo):
    e = run(repo, "fake-ok")
    assert e.status == "ok", e.reason
    assert e.verify["passed"] is True and "hello.txt" in e.files_changed
    assert Path(e.log_dir, "envelope.json").exists()


def test_run_liar_fails(env, repo):
    e = run(repo, "fake-liar")
    assert e.status == "failed" and "done_when failed" in e.reason and e.worker_status == "done"


def test_quota_sets_cooldown_then_skips(env, repo):
    e = run(repo, "fake-quota")
    assert e.status == "error" and e.reason.startswith("quota")
    assert health.in_cooldown("fake-quota")
    assert run(repo, "fake-quota").status == "skipped"


def test_timeout_kills(env, repo):
    start = time.monotonic()
    e = run(repo, "fake-sleep", timeout_s=1)
    assert e.status in ("timeout",) and time.monotonic() - start < 120


def test_refusals(env, repo):
    assert run(repo, "fake-off").status == "skipped"
    assert run(repo, "fake-claude").status == "skipped"


def test_auto_fallback(env, repo):
    # fake-quota (rank 1) then fake-error (rank 2) then fake-gpu (rank 5) then fake-ok
    e = run(repo, "auto")
    assert e.status == "ok", e.attempts
    assert [a["worker"] for a in e.attempts][:2] == ["fake-quota", "fake-error"]


def test_auto_failed_does_not_fall_back(env, repo):
    reg = registry.load()
    card = TaskCard(goal="x", cwd=str(repo), done_when=["exit 1"])
    for w in ("fake-quota", "fake-error"):
        health.set_cooldown(w, 600, "test")
    e = run_card(reg, card, "auto", RunOptions(gpu_busy=False))
    assert e.status == "failed" and len(e.attempts) == 1


def test_router_reasons(env):
    health.set_cooldown("fake-ok", 600, "test")
    cands = {c.worker: c for c in router.candidates(registry.load(), "code", gpu_busy=True)}
    assert cands["fake-off"].reason == "disabled"
    assert "opt-in" in cands["fake-claude"].reason
    assert cands["fake-ok"].reason.startswith("cooldown")
    assert "GPU busy" in cands["fake-gpu"].reason
    assert cands["fake-liar"].eligible


def test_redaction(env, repo, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "supersecretvalue123")
    card = TaskCard(goal="echo supersecretvalue123 please", cwd=str(repo))
    e = run_card(registry.load(), card, "fake-echo", RunOptions(gpu_busy=False))
    for f in Path(e.log_dir).iterdir():
        assert "supersecretvalue123" not in f.read_text(encoding="utf-8")


def test_ledger_summary(env, repo, tmp_path):
    run(repo, "fake-ok")
    other = tmp_path / "other"
    other.mkdir()
    run(other, "fake-liar")
    s = ledger.summary()
    assert s["per_worker"]["run:fake-ok"]["ok"] == 1 and s["per_worker"]["run:fake-liar"]["failed"] == 1
    assert s["share_by_cost"] == {"free": 1.0}


def test_worktree_isolation(env, repo):
    e1 = run(repo, "fake-ok", worktree=True)
    e2 = run(repo, "fake-ok", worktree=True)
    assert e1.status == e2.status == "ok"
    assert e1.worktree["branch"] != e2.worktree["branch"] and e1.worktree["commit"]
    assert not (repo / "hello.txt").exists()  # main checkout untouched


def test_cli_workers_json(env, capsys):
    assert main(["workers", "--json"]) == 0
    ids = {r["id"] for r in json.loads(capsys.readouterr().out)}
    assert "fake-ok" in ids


def test_cli_route_json(env, capsys):
    main(["route", "--capability", "code", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["chosen"] and data["candidates"]


@pytest.fixture(autouse=True)
def _no_comfy(monkeypatch):
    monkeypatch.setattr(router, "comfy_busy", lambda *a, **k: False)
    os.environ.pop("FLEET_BOARD", None)


def test_followup_in_worktree_commits(env, repo):
    first = run(repo, "fake-liar", worktree=True, done_when=[])
    wt = Path(first.worktree["path"])
    e = run(wt, "fake-ok")
    assert e.status == "ok" and e.worktree["commit"]


def test_repair_round_fixes_failed_checks(env, repo):
    e = run(repo, "fake-half")
    assert e.status == "ok", e.reason
    assert (repo / "hello.txt").read_text() == "hello"
    assert run(repo, "fake-half", repair_rounds=0).status in ("ok", "failed")


def test_repair_off_leaves_failure(env, repo, tmp_path):
    (repo / "hello.txt").unlink(missing_ok=True)
    e = run(repo, "fake-half", repair_rounds=0)
    assert e.status == "failed"


def test_empty_reply_is_error_so_auto_falls_back(env, repo):
    e = run(repo, "fake-silent")
    assert e.status == "error" and "empty reply" in e.reason


def test_worktree_links_ignored_dirs(env, repo):
    (repo / ".gitignore").write_text("assets/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ignore"], cwd=repo, check=True)
    (repo / "assets").mkdir()
    (repo / "assets" / "big.bin").write_text("data", encoding="utf-8")
    e = run(repo, "fake-ok", worktree=True)
    wt = Path(e.worktree["path"])
    assert (wt / "assets" / "big.bin").read_text() == "data"
    assert e.status == "ok" and e.worktree["commit"]
    shown = subprocess.run(
        ["git", "show", "--stat", "--format=", e.worktree["commit"]], cwd=wt, capture_output=True, text=True
    ).stdout
    assert "assets" not in shown
    # pruning the merged worktree removes only the links, never the main tree's assets
    subprocess.run(["git", "merge", "-q", e.worktree["branch"]], cwd=repo, check=True)
    assert e.worktree["branch"] in worktree.prune_merged(repo)
    assert (repo / "assets" / "big.bin").read_text() == "data" and not wt.exists()


def test_empty_answer_cut_at_max_tokens_is_an_error():
    from ai_neural_network.llm import ProviderError, _text

    assert _text({"choices": [{"message": {"content": " hi "}, "finish_reason": "stop"}]}) == "hi"
    for empty in ({"content": ""}, {"content": None, "reasoning_content": "all thinking"}):
        with pytest.raises(ProviderError, match="empty answer"):
            _text({"choices": [{"message": empty, "finish_reason": "stop"}]})
    with pytest.raises(ProviderError, match="empty answer"):
        _text({"choices": [{"message": {"content": "  "}}]})  # no finish_reason
    with pytest.raises(ProviderError, match="max_tokens"):
        _text({"choices": [{"message": {"content": None, "reasoning_content": "thinking"}, "finish_reason": "length"}]})


def _tracked_test(repo):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_hello.py").write_text("def test_hello():\n    assert False\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "test"], cwd=repo, check=True)


def test_editing_own_check_is_refuted(env, repo):
    _tracked_test(repo)
    card = TaskCard(goal="make the test pass", cwd=str(repo), done_when=["python -c 0"])
    e = run_card(registry.load(), card, "fake-cheat", RunOptions(gpu_busy=False))
    assert e.status == "failed" and "refuted" in e.reason and "tests/test_hello.py" in e.reason


def test_editing_test_in_scope_is_allowed(env, repo):
    _tracked_test(repo)
    card = TaskCard(goal="fix the test", cwd=str(repo), paths=["tests/test_hello.py"], done_when=["python -c 0"])
    e = run_card(registry.load(), card, "fake-cheat", RunOptions(gpu_busy=False))
    assert e.status == "ok", e.reason


def test_edited_checks_rules():
    from ai_neural_network.verify import edited_checks

    old = lambda f: f != "tests/test_new.py"  # noqa: E731
    cmds = ["python check.py", "pytest -q tests/test_a.py"]
    changed = ["src/x.py", "check.py", "tests/test_a.py", "tests/test_new.py", "lib/test_b.py", "docs/a.md"]
    assert edited_checks(changed, cmds, [], old) == ["check.py", "tests/test_a.py", "lib/test_b.py"]
    assert edited_checks(changed, cmds, ["lib", "check.py"], old) == ["tests/test_a.py"]


def test_leader_tiers_and_election(env, monkeypatch):
    from ai_neural_network import leader

    reg = registry.load(Path(__file__).parents[1] / "src" / "ai_neural_network" / "workers.yaml")
    assert "github" in reg.workers
    assert {"claude-leader", "codex-leader", "github-leader", "muse", "go-pro"} <= set(reg.tiers)
    monkeypatch.setattr("ai_neural_network.leader.shutil.which", lambda name: f"C:/bin/{name}.exe")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OLLAMA_API_KEY", "ol-test")
    assert leader.elect_leader(reg)["elected"] == "claude-leader"
    health.set_cooldown("claude", 600, "5-hour limit")
    assert leader.elect_leader(reg)["elected"] == "codex-leader"
    health.set_cooldown("gpt", 600, "weekly limit")
    assert leader.elect_leader(reg)["elected"] == "gemini-argon"


def test_retry_at_claude_and_copilot_argv(monkeypatch):
    from ai_neural_network.llm import _cli_argv, _retry_at_s

    secs = _retry_at_s("You've hit your 5-hour limit · resets 3pm (America/Denver)")
    assert secs is not None and 60.0 <= secs <= 86500.0
    reg = registry.load(Path(__file__).parents[1] / "src" / "ai_neural_network" / "workers.yaml")
    monkeypatch.setattr("shutil.which", lambda name: "C:/bin/copilot.exe")
    argv, stdin_text, _ = _cli_argv(reg.tiers["github-leader"], "hello", Path("."), [])
    assert argv == ["C:/bin/copilot.exe", "-p", "hello", "-s"] and stdin_text is None


def test_version_matches_pyproject():
    import tomllib

    from ai_neural_network import __version__

    root = Path(__file__).parents[1]
    with open(root / "pyproject.toml", "rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    assert __version__ == declared
