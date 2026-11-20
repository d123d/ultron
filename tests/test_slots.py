"""Provider slots, job slots, ad-hoc provider:model tiers."""

import pytest

from ai_neural_network import registry, slots
from ai_neural_network.llm import AskResult, candidates
from ai_neural_network.procs import FleetError


def test_one_slot_per_provider_with_models():
    reg = registry.load()
    rows = slots.provider_slots(reg)
    names = [r["provider"] for r in rows]
    assert len(names) == len(set(names))  # exactly one slot per provider
    by = {r["provider"]: r for r in rows}
    assert {"nvidia", "openrouter", "strata", "claude", "codex", "agy", "copilot"} <= set(by)
    assert "nim-ultra" in by["nvidia"]["tiers"] and "nvidia/nemotron-3-ultra-550b-a55b" in by["nvidia"]["models"]
    assert sum(len(r["tiers"]) for r in rows) == len(reg.tiers)  # every tier sits in exactly one slot


def test_adhoc_provider_model_tier():
    reg = registry.load()
    assert candidates(reg, "nvidia:some/new-model,") == ["nvidia:some/new-model"]
    t = reg.tiers["nvidia:some/new-model"]
    assert t.model == "some/new-model" and t.key_env == "NVIDIA_API_KEY" and not t.fallback
    with pytest.raises(FleetError):
        candidates(reg, "nosuchprovider:model,")


def test_shipped_jobs_reference_real_tiers_and_capabilities():
    assert slots.check_jobs(registry.load()) == []


def test_job_falls_through_to_next_model(monkeypatch):
    calls = []

    def fake_ask(reg, spec, prompt, **kw):
        calls.append(spec)
        if len(calls) == 1:
            raise FleetError("first model down")
        return AskResult(text="done", tier=spec.rstrip(","), model="m")

    monkeypatch.setattr(slots, "ask", fake_ask)
    monkeypatch.setattr(slots.ledger, "append", lambda row: None)
    out = slots.run_job(registry.load(), "quick", "hi")
    first, second = slots.load_jobs()["quick"]["tiers"][:2]
    assert calls == [first + ",", second + ","]
    assert out["status"] == "ok" and out["by"] == second and len(out["skipped"]) == 1
    with pytest.raises(FleetError):
        slots.run_job(registry.load(), "nosuchjob", "hi")


def test_every_harness_has_a_slot():
    reg = registry.load()
    rows = slots.harness_slots(reg)
    assert [r["harness"] for r in rows] == list(reg.workers)
    assert {"opencode", "codex", "agy", "pi", "claude", "github", "muse-code", "openhuman"} <= {r["harness"] for r in rows}


def test_learning_never_writes_into_the_repo_config(env):
    from ai_neural_network.network import Network
    from ai_neural_network.paths import config_dir

    net = Network.from_config()
    assert net.learned_path().parent == env[1]  # state dir, not config/
    before = (config_dir() / "weights.json").read_text(encoding="utf-8")
    net.sleep()
    assert (config_dir() / "weights.json").read_text(encoding="utf-8") == before and net.learned_path().exists()


def test_redact_covers_the_secrets_file(tmp_path, monkeypatch):
    from ai_neural_network import procs

    fake = "abcd1234" + "efgh5678"  # split so secret scanners do not flag a fixture
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / ".env").write_text("SOME_API_KEY=" + fake + "\nAWS_REGION=us-east-1-region\n", encoding="utf-8")
    monkeypatch.setattr(procs.Path, "home", lambda: tmp_path)
    assert procs.redact(f"error: key {fake} rejected") == "error: key [REDACTED] rejected"
    assert procs.redact("us-east-1-region") == "us-east-1-region"


def test_worker_prompt_lists_matching_skills(tmp_path, monkeypatch):
    from ai_neural_network.card import TaskCard, render_prompt

    (tmp_path / "blender").mkdir()
    (tmp_path / "blender" / "SKILL.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("ULTRON_SKILLS_DIR", str(tmp_path))
    prompt = render_prompt(TaskCard(goal="Render the Blender kit"), None)
    assert str(tmp_path / "blender" / "SKILL.md") in prompt  # installed + topic matches
    assert "world-builder" not in prompt  # topic matches but not installed here: skipped
    assert "SKILLS" not in render_prompt(TaskCard(goal="say hello"), None)


def test_reclaim_reopens_cards_of_dead_worker_loops(monkeypatch):
    import os
    import socket

    host = socket.gethostname()
    issues = [
        {"id": "a", "assignee": f"fleet-{host}-999999999"},  # dead pid: stuck card
        {"id": "b", "assignee": f"fleet-{host}-{os.getpid()}"},  # live loop: leave it
        {"id": "c", "assignee": "fleet-otherbox-1"},
    ]  # another machine: not ours to judge

    class FakeBoard:
        reopened: list = []

        def list(self, status):
            return issues

        def reopen(self, issue_id, actor, note):
            self.reopened.append(issue_id)

    assert slots.reclaim_stale(FakeBoard()) == ["a"]


def test_qc_fails_on_bad_or_missing_renders(tmp_path, monkeypatch):
    reg = registry.load()
    assert slots.qc_images(reg, str(tmp_path))["ok"] is False  # no render at all is not a pass
    (tmp_path / "good.png").write_bytes(b"x")
    (tmp_path / "bad.png").write_bytes(b"x")
    monkeypatch.setattr(
        slots,
        "run_job",
        lambda reg, kind, prompt, images, **kw: {
            "text": "a dome\nVERDICT: PASS" if "good" in images[0] else "grey\nVERDICT: FAIL - flat colour"
        },
    )
    out = slots.qc_images(reg, str(tmp_path))
    assert out["checked"] == 2 and out["ok"] is False
    (tmp_path / "bad.png").unlink()
    assert slots.qc_images(reg, str(tmp_path))["ok"] is True


def test_job_order_demotes_a_model_that_keeps_failing(monkeypatch):
    rows = [{"type": "job-try", "job": "quick", "tier": "a", "status": "error"}] * 3
    monkeypatch.setattr(slots.ledger, "read", lambda: rows)
    assert slots._by_results("quick", ["a", "b", "c"]) == ["b", "c", "a"]  # a goes last, still tried
    assert slots._by_results("reason", ["a", "b"]) == ["a", "b"]  # other jobs keep their order
    monkeypatch.setattr(slots.ledger, "read", lambda: rows + [{"type": "job", "job": "quick", "tier": "a", "status": "ok"}])
    assert slots._by_results("quick", ["a", "b", "c"]) == ["a", "b", "c"]  # one success restores it


def test_failed_card_rotates_to_the_next_harness(env, repo):
    from ai_neural_network.card import TaskCard
    from ai_neural_network.runner import RunOptions, run_card

    reg = registry.load()
    first = run_card(reg, TaskCard(goal="say hi", cwd=str(repo)), "auto", RunOptions())
    second = run_card(reg, TaskCard(goal="say hi", cwd=str(repo)), "auto", RunOptions(rotate=1))
    assert first.attempts[0]["worker"] != second.attempts[0]["worker"]


def test_worker_that_did_nothing_is_an_error_not_a_failed_card(env, repo):
    from ai_neural_network import health
    from ai_neural_network.card import TaskCard
    from ai_neural_network.runner import RunOptions, run_card

    reg = registry.load()
    card = TaskCard(goal="write hello", cwd=str(repo), done_when=['python -c "import sys; sys.exit(1)"'], capability="stall")
    stalled = run_card(reg, card, "fake-stall", RunOptions())
    assert stalled.status == "error" and "did no work" in stalled.reason and health.in_cooldown("fake-stall")


def test_provider_model_slots_top_and_cheap(monkeypatch):
    reg = registry.load()
    assert slots.model_slot(reg, "nvidia", "top").startswith("nvidia:")
    assert slots.model_slot(reg, "claude", "top") in reg.tiers  # CLI provider slots name a tier
    assert candidates(reg, "nvidia:cheap,")[0].startswith("nvidia:")
    monkeypatch.setattr(slots, "_secret_or_none", lambda k: "x")
    top = slots.expand_slots(reg, "top")
    assert top and len(top) == len(set(top))
    assert not any(t.startswith("openrouter:anthropic/") for t in top)  # metered slot never in *:top
    assert all(reg.tiers[t].cost in ("free", "flat") for t in candidates(reg, ",".join(top) + ","))
    assert slots.job_specs(reg, ["nim-ultra", "*:cheap"])[0] == "nim-ultra"


def test_stopped_worker_hands_partial_work_to_the_next():
    from ai_neural_network.card import TaskCard
    from ai_neural_network.envelope import Envelope
    from ai_neural_network.runner import continue_card

    env = Envelope.__new__(Envelope)
    env.worker, env.reason, env.files_changed, env.worktree = "pi", "timeout", ["a.py"], {"path": "/wt/1"}
    nxt = continue_card(TaskCard(goal="build it", cwd="/repo"), env)
    assert nxt.cwd == "/wt/1" and not nxt.worktree and "CONTINUE" in nxt.goal and "a.py" in nxt.goal
