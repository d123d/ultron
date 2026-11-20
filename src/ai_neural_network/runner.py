"""One fleet run: card -> worker -> process -> parse -> verify -> diff -> envelope -> ledger."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import health, ledger, router, worktree
from .adapters import get_adapter
from .card import TaskCard, render_prompt
from .envelope import Envelope, parse_fleet_result
from .gitinfo import diff_since, existed_at, snapshot
from .paths import runs_dir
from .procs import FleetError, redact, run_process
from .registry import Registry
from .verify import REFUTED, edited_checks, run_checks


@dataclass
class RunOptions:
    allow_claude: bool = False
    budget_usd: float = 1.0
    timeout_s: int | None = None
    worktree: bool | None = None
    max_attempts: int = 0  # 0 = every eligible worker in turn (work keeps going while any harness answers)
    repair_rounds: int = 1
    gpu_busy: bool | None = None
    rotate: int = 0  # auto: start this many places down the eligible list (a card that failed goes elsewhere next)


def new_run_id() -> str:
    return f"r-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _ledger(env: Envelope, cost: str) -> None:
    ledger.append(
        {
            "type": "run",
            "id": env.run_id,
            "card_id": env.card_id,
            "worker": env.worker,
            "cost": cost,
            "status": env.status,
            "duration_s": env.duration_s,
            "input_tokens": env.usage.get("input_tokens"),
            "output_tokens": env.usage.get("output_tokens"),
            "cost_usd": env.usage.get("cost_usd"),
            "reason": (env.reason or "")[:200],
        }
    )


def run_once(reg: Registry, card: TaskCard, worker_id: str, opts: RunOptions) -> Envelope:
    run_id = new_run_id()
    started = health.iso(health.now())
    worker = reg.workers.get(worker_id)
    if worker is None:
        raise FleetError(f"unknown worker {worker_id!r} (see `fleet workers`)")
    base = Envelope(
        run_id=run_id, card_id=card.id, worker=worker.id, control=worker.control, status="skipped", started_at=started
    )

    def refuse(reason: str) -> Envelope:
        env = replace(base, reason=reason)
        _ledger(env, worker.cost)
        return env

    if not worker.enabled:
        return refuse("worker disabled in registry")
    if worker.opt_in and not opts.allow_claude:
        return refuse("opt-in worker (Claude is operator-only); pass --allow-claude")
    until = health.cooldown_until(worker.id)
    if until:
        return refuse(f"cooldown until {health.iso(until)}")

    log_dir = runs_dir() / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    cwd = Path(card.cwd).resolve()
    wt_info = None
    use_wt = card.worktree if opts.worktree is None else opts.worktree
    if use_wt:
        _root, cwd, branch = worktree.create(cwd, run_id)
        wt_info = {"path": str(cwd), "branch": branch, "commit": None}
    run_card = replace(card, cwd=str(cwd))
    prompt = redact(render_prompt(run_card, worker))
    prompt_file = log_dir / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    (log_dir / "card.json").write_text(redact(json.dumps(run_card.to_dict(), indent=2)), encoding="utf-8")

    snap = snapshot(cwd)
    timeout = int(opts.timeout_s or card.timeout_s or worker.timeout_s)
    adapter = get_adapter(worker.control)
    try:
        inv = adapter.build(
            run_card, worker, prompt, prompt_file, str(cwd), timeout, allow_claude=opts.allow_claude, budget_usd=opts.budget_usd
        )
        grace = 60 if worker.control == "acpx" else 0  # acpx enforces its own timeout first
        proc = run_process(inv.argv, cwd, log_dir, env=inv.env, timeout_s=timeout + grace, stdin_text=inv.stdin_text)
    except FleetError as e:
        env = replace(base, status="error", reason=str(e), log_dir=str(log_dir), worktree=wt_info)
        (log_dir / "envelope.json").write_text(env.to_json(), encoding="utf-8")
        _ledger(env, worker.cost)
        return env
    parsed = adapter.parse(proc)
    result = parse_fleet_result(parsed.reply) or {}
    env = replace(
        base,
        log_dir=str(log_dir),
        duration_s=proc.duration_s,
        usage=parsed.usage,
        session_id=parsed.session_id,
        worker_status=result.get("status"),
        summary=str(result.get("summary") or parsed.reply[-300:]).strip(),
        worktree=wt_info,
    )

    quota = health.detect_quota("\n".join([parsed.reply, proc.stderr, parsed.error or ""]), worker.quota_patterns)
    if proc.timed_out or quota:  # partial work a stopped worker leaves behind is handed to the next one
        env.files_changed, env.diffstat = diff_since(snap)
    if proc.timed_out:
        env.status, env.reason = "timeout", f"no result within {timeout}s; process tree killed"
        health.record_error(worker.id, env.reason)
    elif quota and (parsed.error or proc.rc not in (0, None) or not result):
        env.status, env.reason = "error", f"quota: {quota}"
        health.set_cooldown(worker.id, worker.cooldown_s, env.reason)
    elif parsed.error and not parsed.reply:
        env.status, env.reason = "error", parsed.error[:300]
        health.record_error(worker.id, env.reason)
    else:
        env.verify = run_checks(card.done_when, cwd)
        files, stat = diff_since(snap)
        env.files_changed, env.diffstat = files, stat
        if not parsed.reply.strip() and not files:
            env.status, env.reason = "error", "empty reply and no file changes"
            health.record_error(worker.id, env.reason)
        elif env.verify["ran"] and not env.verify["passed"] and not files and not result:
            # No file change and no FLEET_RESULT: the worker never worked. Pi, out of model quota, printed
            # "Retrying..." for 30 s and exited 0; that counted as the card failing and parked nine cards in
            # 45 minutes (2026-11-15). An error cools the worker and lets the next harness take the card.
            env.status, env.reason = "error", "worker did no work (no file change, no FLEET_RESULT)"
            health.set_cooldown(worker.id, worker.cooldown_s, env.reason)
        elif env.verify["ran"] and not env.verify["passed"]:
            failed = [r["cmd"] for r in env.verify["results"] if r["rc"] != 0]
            env.status, env.reason = "failed", f"done_when failed: {failed[0]}"
        elif env.verify["ran"] and (gamed := edited_checks(files, card.done_when, card.paths, lambda f: existed_at(snap, f))):
            env.status, env.reason = "failed", f"{REFUTED}: worker edited check file(s) outside scope: {', '.join(gamed[:5])}"
        elif env.worker_status == "failed":
            env.status, env.reason = "failed", "worker reported failed"
        elif parsed.error:
            env.status, env.reason = "error", parsed.error[:300]
        else:
            env.status = "ok"
            env.reason = "verified by done_when" if env.verify["ran"] else "worker finished (no done_when checks)"
        if env.status != "error":
            health.record_ok(worker.id)
    if wt_info is None and env.status == "ok" and "/.fleet/worktrees/" in str(cwd).replace("\\", "/"):
        wt_info = {"path": str(cwd), "branch": None, "commit": None}  # follow-up run inside an existing fleet worktree
        env.worktree = wt_info
    if wt_info and env.status == "ok":
        wt_info["commit"] = worktree.commit_all(cwd, f"fleet {run_id}: {card.title}")
    (log_dir / "envelope.json").write_text(env.to_json(), encoding="utf-8")
    _ledger(env, worker.cost)
    return env


def repair_card(card: TaskCard, env: Envelope) -> TaskCard:
    """Follow-up card: same goal, run in the failed run's tree, with the failing check output attached."""
    fails = [r for r in (env.verify or {}).get("results", []) if r["rc"] != 0]
    notes = "\n".join(f"$ {r['cmd']}\n{r['tail'][-1500:]}" for r in fails[:3])
    cwd = env.worktree["path"] if env.worktree else card.cwd
    goal = (
        f"{card.goal}\n\nREPAIR: a previous attempt left changes in this tree but these done_when checks failed. "
        f"Fix the cause (do not weaken or skip the checks), then re-run them.\n{notes}"
    )
    return replace(card, goal=goal, cwd=cwd, worktree=False)


def continue_card(card: TaskCard, env: Envelope) -> TaskCard:
    """Hand-over card: a worker stopped partway (error/timeout) after changing files; the next worker carries on
    in the same tree instead of starting over."""
    cwd = env.worktree["path"] if env.worktree else card.cwd
    goal = (
        f"{card.goal}\n\nCONTINUE: a previous agent ({env.worker}) stopped partway ({env.reason[:300]}). "
        f"Its changes are already in this tree ({', '.join(env.files_changed[:20])}). Check them, keep what is "
        f"right and finish the goal."
    )
    return replace(card, goal=goal, cwd=cwd, worktree=False)


def _with_repair(reg: Registry, card: TaskCard, env: Envelope, opts: RunOptions) -> Envelope:
    for _ in range(opts.repair_rounds):
        # a refuted run must not be repaired: the next round's snapshot would hide the edited check
        if env.status != "failed" or not (env.verify or {}).get("ran") or not env.files_changed or env.reason.startswith(REFUTED):
            break
        env = run_once(reg, repair_card(card, env), env.worker, opts)
    return env


def run_card(reg: Registry, card: TaskCard, worker_id: str | None = None, opts: RunOptions | None = None) -> Envelope:
    """Run a card. worker_id 'auto' walks the router's candidate list, falling back on error/timeout/skipped."""
    opts = opts or RunOptions()
    wid = worker_id or card.worker or "opencode"
    if wid != "auto":
        return _with_repair(reg, card, run_once(reg, card, wid, opts), opts)
    attempts: list[dict] = []
    env: Envelope | None = None
    cands = router.candidates(reg, card.capability, allow_claude=opts.allow_claude, gpu_busy=opts.gpu_busy)
    eligible = [c for c in cands if c.eligible]
    shift = opts.rotate % len(eligible) if eligible else 0
    work = card
    for cand in (eligible[shift:] + eligible[:shift])[: opts.max_attempts or None]:
        env = _with_repair(reg, work, run_once(reg, work, cand.worker, opts), opts)
        attempts.append({"worker": env.worker, "status": env.status, "reason": env.reason, "run_id": env.run_id})
        if env.status not in ("error", "timeout", "skipped"):
            break
        if env.files_changed and env.status in ("error", "timeout"):
            work = continue_card(card, env)  # next harness picks up the partial work, not a blank tree
    if env is None:
        reasons = "; ".join(f"{c.worker}: {c.reason}" for c in cands)
        raise FleetError(f"no eligible worker for {card.capability}: {reasons}")
    env.attempts = attempts
    if env.log_dir:
        Path(env.log_dir, "envelope.json").write_text(env.to_json(), encoding="utf-8")
    return env
