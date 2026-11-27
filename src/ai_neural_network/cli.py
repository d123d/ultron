"""brain CLI: brain-structured AI orchestration across cortical regions and workers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import health, leader, ledger, registry, router
from .board import Board
from .card import TaskCard, load_card
from .doctor import doctor
from .evals import evaluate
from .llm import ask, ask_many, ask_stream, vote
from .network import Network
from .procs import FleetError, ensure_full_path
from .runner import RunOptions, run_card
from .work import batch, load_jsonl, work


def _out(data, as_json: bool, text: str | None = None) -> None:
    if as_json or text is None:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        print(text)


def _table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "(none)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    lines = ["  ".join(c.ljust(widths[c]) for c in cols)]
    lines += ["  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols) for r in rows]
    return "\n".join(lines)


def _card(args) -> TaskCard:
    if getattr(args, "card", None):
        card = load_card(Path(args.card))
    elif getattr(args, "text", None):
        card = TaskCard(goal=args.text)
    else:
        raise FleetError("give a card path or --text")
    if getattr(args, "cwd", None):
        card.cwd = str(Path(args.cwd).resolve())
    if getattr(args, "done_when", None):
        card.done_when = [*card.done_when, *args.done_when]
    for attr in ("capability", "timeout"):
        val = getattr(args, attr, None)
        if val:
            setattr(card, "timeout_s" if attr == "timeout" else attr, val)
    if getattr(args, "path", None):
        card.paths = [*card.paths, *args.path]
    return card


def _envelope_text(env) -> str:
    lines = [f"[{env.status}] {env.worker} run {env.run_id} ({env.duration_s}s): {env.reason}"]
    if env.summary:
        lines.append(f"summary: {env.summary[:300]}")
    if env.files_changed:
        lines.append(f"files: {', '.join(env.files_changed[:12])}  {env.diffstat}")
    if env.verify.get("ran"):
        lines.append(f"verify: {'passed' if env.verify['passed'] else 'FAILED'}")
    if env.attempts and len(env.attempts) > 1:
        lines.append("attempts: " + " -> ".join(f"{a['worker']}:{a['status']}" for a in env.attempts))
    lines.append(f"log: {env.log_dir}")
    return "\n".join(lines)


def _opts(args) -> RunOptions:
    return RunOptions(
        allow_claude=getattr(args, "allow_claude", False),
        budget_usd=getattr(args, "budget_usd", 1.0),
        timeout_s=getattr(args, "timeout", None),
        worktree=getattr(args, "worktree", None),
    )


def _isolated_test_env() -> bool:
    return bool(os.environ.get("FLEET_STATE_DIR") or os.environ.get("PYTEST_CURRENT_TEST"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="brain", description=__doc__)
    p.add_argument("--registry", help="registry yaml (default: package workers.yaml or BRAIN_REGISTRY)")
    p.add_argument("--config", help="neural network config directory (default: repo config/)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="cortical regions, neurons, synapses, weights, and elected leader")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("excite", help="inject a stimulus and propagate activation across regions")
    s.add_argument("stimulus")
    s.add_argument("--capability")
    s.add_argument("--entry")
    s.add_argument("--intensity", type=float, default=1.0)
    s.add_argument("--cooldowns", action="store_true", help="apply brainstem cooldowns before excitation")
    s.add_argument("--stakes", choices=["high"], help="thalamic gating: high = only prefrontal recruited easily")
    s.add_argument("--carry", action="store_true", help="working memory: start warm from the previous population")
    s.add_argument("--trace", action="store_true")
    s.add_argument("--json", action="store_true")

    for name in ("fire", "pulse"):
        s = sub.add_parser(name, help="propagate stimulus, invoke recruited neuron, check, and update weights")
        s.add_argument("stimulus")
        s.add_argument("--capability")
        s.add_argument("--entry")
        s.add_argument("--intensity", type=float, default=1.0)
        s.add_argument("--tier")
        s.add_argument("--worker")
        s.add_argument("--cwd")
        s.add_argument("--done-when", action="append")
        s.add_argument("--system")
        s.add_argument("--schema")
        s.add_argument("--timeout", type=int, default=180)
        s.add_argument("--allow-claude", action="store_true")
        s.add_argument("--private", action="store_true", help="privacy gate: execute only on local tiers (enforced in code)")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--json", action="store_true")

    s = sub.add_parser("learn", help="inspect demo or apply Hebbian weight update")
    s.add_argument("--demo", action="store_true")
    s.add_argument("--edge", help="synapse edge A->B to update")
    grp = s.add_mutually_exclusive_group()
    grp.add_argument("--success", action="store_true", default=True)
    grp.add_argument("--fail", action="store_true")
    s.add_argument("--rate", type=float, default=0.05)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("sleep", help="sleep consolidation: decay weights, prune faded synapses, save weights.json")
    s.add_argument("--decay", type=float, default=0.01)
    s.add_argument("--prune-below", type=float, default=0.05)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("sync", help="neurogenesis check: registry workers/tiers that have no neuron yet")
    s.add_argument("--add", help="scaffold a neuron (+ synapse) for this registry name and exit")
    s.add_argument("--region", default="association")
    s.add_argument("--capability", action="append", default=[])
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("workers", help="list neurons/workers and tiers")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("doctor", help="brainstem homeostasis / live health and quota table")
    s.add_argument("--live", action="store_true", help="also make one tiny call per LLM tier")
    s.add_argument("--catalog", action="store_true", help="check tier models against models.dev and OpenRouter expiry")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("route", help="explain candidate ordering / neural affinity for a capability")
    s.add_argument("--capability", default="code")
    s.add_argument("--allow-claude", action="store_true")
    s.add_argument("--json", action="store_true")

    def run_args(sp, single: bool = True):
        if single:
            sp.add_argument("card", nargs="?", help="task card .yaml/.json")
            sp.add_argument("--text", help="goal text instead of a card file")
            sp.add_argument("--cwd")
            sp.add_argument("--done-when", action="append", help="check command (repeatable)")
            sp.add_argument("--path", action="append", help="scope path (repeatable)")
            sp.add_argument("--capability")
        sp.add_argument("--worker", help="worker id or auto")
        sp.add_argument("--timeout", type=int)
        sp.add_argument("--worktree", action="store_true", default=None, help="isolate in a git worktree")
        sp.add_argument("--allow-claude", action="store_true")
        s_budget = sp
        s_budget.add_argument("--budget-usd", type=float, default=1.0)
        sp.add_argument("--json", action="store_true")

    run_args(sub.add_parser("run", help="dispatch one task card and update neural weights"))
    s = sub.add_parser("batch", help="run a JSONL file of task cards")
    s.add_argument("file")
    s.add_argument("--parallel", type=int, default=2)
    run_args(s, single=False)

    s = sub.add_parser("ask", help="direct LLM call by tier (throttled per provider)")
    s.add_argument("prompt", nargs="?")
    s.add_argument("--tier", default="utility")
    s.add_argument("--prompt-file")
    s.add_argument("--system")
    s.add_argument("--schema", help="JSON schema file")
    s.add_argument("--file", action="append", default=[])
    s.add_argument("--image", action="append", default=[])
    s.add_argument("--private", action="store_true")
    s.add_argument("--stream", action="store_true", help="stream answer deltas as they arrive (plain HTTP tiers)")
    s.add_argument("--vote", action="store_true")
    s.add_argument("--many")
    s.add_argument("--max-tokens", type=int)
    s.add_argument("--timeout", type=int, default=180)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("ledger", help="hippocampus episode ledger")
    s.add_argument("--summary", action="store_true")
    s.add_argument("--usage", action="store_true")
    s.add_argument("--tail", type=int, default=20)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("eval", help="score tiers on fixed tasks")
    s.add_argument("--tiers", required=True)
    s.add_argument("--only")
    s.add_argument("--timeout", type=int, default=180)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("providers", help="cloud provider access: key presence, adapter, cooldown per tier")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("slots", help="provider slots: one per provider, with concurrency and its model list")
    s.add_argument("--refresh", action="store_true", help="fetch every provider's live /models list first")
    s.add_argument("--models", help="list every model of this provider")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("jobs", help="job slots: kinds of work and the models each one uses")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("job", help="do one piece of work in a job slot (best model for that kind, with fallthrough)")
    s.add_argument("kind")
    s.add_argument("prompt", nargs="?")
    s.add_argument("--prompt-file")
    s.add_argument("--cwd")
    s.add_argument("--done-when", action="append")
    s.add_argument("--system")
    s.add_argument("--file", action="append", default=[])
    s.add_argument("--image", action="append", default=[])
    s.add_argument("--timeout", type=int)
    s.add_argument("--allow-claude", action="store_true")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("qc", help="vision QC of the newest renders under a folder; exit 1 on any FAIL (use as --done-when)")
    s.add_argument("root", nargs="?", default=".")
    s.add_argument("--since", type=int, default=180, help="only images changed in the last N minutes")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--subject", default="")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("daemon", help="background loop (started at Windows logon): drain the board, heartbeat")
    s.add_argument("--interval", type=int, default=60)
    s.add_argument("--once", action="store_true")

    s = sub.add_parser("bakeoff", help="score tiers on the fixed eval set; --apply feeds scores into synapses")
    s.add_argument("--tiers", required=True)
    s.add_argument("--only")
    s.add_argument("--timeout", type=int, default=180)
    s.add_argument("--apply", action="store_true")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("spend", help="today's estimated metered spend and the cost-neuromodulation scalar")
    s.add_argument("--budget", type=float, default=5.0)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("remember", help="store an episode in semantic memory (embeddings)")
    s.add_argument("text")
    s.add_argument("--tier", default="openai-embed")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("recall", help="recall episodes similar to a query (embeddings)")
    s.add_argument("query")
    s.add_argument("--k", type=int, default=5)
    s.add_argument("--tier", default="openai-embed")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("weights", help="weights sync: merge another brain's weights.json, or export yours")
    s.add_argument("action", choices=["merge", "export"])
    s.add_argument("path")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("board", help="beads task board")
    s.add_argument("--dir")
    bsub = s.add_subparsers(dest="bcmd", required=True)
    bsub.add_parser("init")
    b = bsub.add_parser("add")
    b.add_argument("card", nargs="?")
    b.add_argument("--text")
    b.add_argument("--cwd")
    b.add_argument("--done-when", action="append")
    b.add_argument("--path", action="append")
    b.add_argument("--capability")
    b.add_argument("--worker")
    b.add_argument("--dep", action="append", default=[])
    b.add_argument("--priority", type=int, default=2)
    b.add_argument("--hold", action="store_true", help="park the card until `board release ID`")
    b.add_argument("--timeout", type=int)
    b.add_argument("--json", action="store_true")
    b = bsub.add_parser("release", help="let a held (or 3x failed) card run")
    b.add_argument("id")
    for name in ("ready", "list"):
        b = bsub.add_parser(name)
        b.add_argument("--status")
        b.add_argument("--json", action="store_true")
    b = bsub.add_parser("show")
    b.add_argument("id")
    b.add_argument("--json", action="store_true")

    s = sub.add_parser("work", help="drain ready board tasks")
    s.add_argument("--dir")
    s.add_argument("--max-tasks", type=int, default=1)
    s.add_argument("--max-minutes", type=float)
    s.add_argument("--actor")
    run_args(s, single=False)

    s = sub.add_parser("worktree", help="list or prune worktrees")
    s.add_argument("action", choices=["list", "prune"])
    s.add_argument("--cwd", default=".")

    s = sub.add_parser("leader", help="prefrontal leader election, wakeup, and GitHub/Telegram/Muse communication")
    lsub = s.add_subparsers(dest="lcmd", required=True)
    le = lsub.add_parser("elect", help="show active leader priority chain")
    le.add_argument("--json", action="store_true")
    lw = lsub.add_parser("wake", help="wake the elected leader with a status report")
    lw.add_argument("report")
    lw.add_argument("--system")
    lw.add_argument("--channel", default="none")
    lw.add_argument("--repo", default="d123d/ultron")
    lw.add_argument("--issue", type=int)
    lw.add_argument("--json", action="store_true")
    lc = lsub.add_parser("comm", help="talk with Muse, GitHub, Telegram, OpenHuman, or Leader")
    lc.add_argument("--to", required=True, choices=["muse", "github", "telegram", "openhuman", "leader"])
    lc.add_argument("--message", default="")
    lc.add_argument("--repo", default="d123d/ultron")
    lc.add_argument("--issue", type=int)
    lc.add_argument("--title")
    lc.add_argument("--label", action="append", default=[])
    lc.add_argument("--read", action="store_true")
    lc.add_argument("--channel", default="none")
    lc.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    ensure_full_path()
    try:
        return _dispatch(args)
    except FleetError as e:
        print(f"brain: {e}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    reg = registry.load(args.registry)
    net = Network.from_config(args.config)

    if args.cmd == "status":
        by_region: dict[str, list[str]] = {}
        cooled = [name for name in net.neurons if health.in_cooldown(name)]
        for n in net.neurons.values():
            by_region.setdefault(n.region, []).append(n.name)
        elect = leader.elect_leader(reg)
        inhib = sum(1 for w in net.synapses.values() if w < 0)
        weights_file = net.learned_path()
        data = {
            "regions": by_region,
            "neurons": len(net.neurons),
            "synapses": len(net.synapses),
            "inhibitory": inhib,
            "cooled": cooled,
            "weights_file": str(weights_file) if weights_file and weights_file.is_file() else None,
            "leader": elect["elected"],
            "leader_model": elect["model"],
        }
        lines = [f"{r}: {', '.join(names)}" for r, names in sorted(by_region.items())]
        lines.append(f"synapses: {len(net.synapses)} ({inhib} inhibitory), leader: {elect['elected']} ({elect['model']})")
        if cooled:
            lines.append(f"refractory/cooldown: {', '.join(cooled)}")
        _out(data, args.json, "\n".join(lines))
        return 0

    if args.cmd == "excite":
        trace = net.excite(
            args.stimulus,
            capability=args.capability,
            entry=args.entry,
            intensity=args.intensity,
            respect_cooldowns=args.cooldowns,
            stakes=args.stakes,
            carry=args.carry,
        )
        data = {
            "stimulus": args.stimulus,
            "capability": args.capability,
            "fired": trace.fired,
            "ticks": trace.ticks,
            "edges_used": [f"{a}->{b}" for a, b in trace.edges_used],
        }
        lines = [f"stimulus: {args.stimulus!r} capability={args.capability}", f"firing population: {trace}"]
        if args.trace:
            lines.append(json.dumps(trace.ticks, indent=2))
        _out(data, args.json, "\n".join(lines))
        return 0

    if args.cmd in ("fire", "pulse"):
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8")) if args.schema else None
        out = net.fire(
            args.stimulus,
            capability=args.capability,
            entry=args.entry,
            intensity=args.intensity,
            tier=args.tier,
            worker=args.worker,
            cwd=args.cwd,
            done_when=args.done_when,
            system=args.system,
            schema=schema,
            timeout_s=args.timeout,
            allow_claude=args.allow_claude,
            private=getattr(args, "private", False),
            dry_run=args.dry_run,
            reg=reg,
        )
        text = f"population: {out['trace']} | chosen={out['chosen']} | success={out['success']}"
        _out(out, args.json, text)
        return 0 if out["success"] else 1

    if args.cmd == "learn":
        if args.edge:
            if "->" not in args.edge:
                raise FleetError("--edge must be A->B")
            a, b = args.edge.split("->", 1)
            if (a, b) not in net.synapses:
                raise FleetError(f"unknown synapse: {args.edge}")
            before = net.synapses[(a, b)]
            ok = not args.fail
            delta = args.rate if ok else -args.rate
            net.synapses[(a, b)] = max(0.0, min(1.0, before + delta))
            saved = None if _isolated_test_env() else str(net.save_weights())
            data = {"edge": args.edge, "before": round(before, 4), "after": round(net.synapses[(a, b)], 4), "saved": saved}
            _out(data, args.json, f"{args.edge}: {before:.3f} -> {net.synapses[(a, b)]:.3f}")
            return 0
        before = dict(net.synapses)
        trace = net.excite("fix the parser", capability="code")
        net.learn(trace, success=True)
        used = set(trace.edges_used)
        changed = [
            {"edge": f"{a}->{b}", "before": before[(a, b)], "after": net.synapses[(a, b)]}
            for (a, b) in before
            if before[(a, b)] != net.synapses[(a, b)] and (a, b) in used
        ]
        lines = [f"demo run fired: {trace}"] + [
            f"  {c['edge']}: {c['before']:.2f} -> {c['after']:.2f} (strengthened)" for c in changed
        ]
        _out({"fired": trace.fired, "changed": changed}, args.json, "\n".join(lines))
        return 0

    if args.cmd == "sleep":
        result = net.sleep(decay=args.decay, prune_below=args.prune_below)
        msg = (
            f"sleep: decayed all weights by {args.decay}; pruned {len(result['pruned'])} synapses; "
            f"weights -> {result['weights_saved']}"
        )
        _out(result, args.json, msg)
        return 0

    if args.cmd == "sync" and args.add:
        from .scaffold import add_neuron

        data = add_neuron(args.add, region=args.region, capabilities=args.capability or ["text"], config_dir=args.config)
        _out(data, args.json, f"neuron {args.add} scaffolded in {data['neurons_file']} (+ synapse main->{args.add})")
        return 0

    if args.cmd == "sync":
        missing = net.sync_missing()
        data = {"missing_neurons": missing}
        msg = "all registry workers/tiers have neurons" if not missing else "no neuron yet for: " + ", ".join(missing)
        _out(data, args.json, msg)
        return 0

    if args.cmd == "workers":
        rows = [
            {
                "id": w.id,
                "kind": w.kind,
                "cost": w.cost,
                "control": w.control,
                "enabled": w.enabled,
                "opt_in": w.opt_in,
                "capabilities": w.capabilities,
                "notes": w.notes,
            }
            for w in reg.workers.values()
        ]
        rows += [
            {
                "id": t.id,
                "kind": "tier",
                "cost": t.cost,
                "control": "http",
                "model": t.model,
                "base_url": t.base_url,
                "notes": t.notes,
            }
            for t in reg.tiers.values()
        ]
        _out(rows, args.json, _table(rows, ["id", "kind", "cost", "control", "enabled", "notes"]))
        return 0

    if args.cmd == "doctor":
        rows = doctor(reg, live=args.live, catalog=args.catalog)
        cool = [r["id"] for r in rows if r["cooldown_until"]]
        summary = f"\n{sum(r['ok'] for r in rows)}/{len(rows)} ok; cooling: {', '.join(cool) or '-'}"
        _out(rows, args.json, _table(rows, ["id", "kind", "cost", "ok", "version", "cooldown_until", "note"]) + summary)
        return 0

    if args.cmd == "route":
        cands = router.candidates(reg, args.capability, allow_claude=args.allow_claude)
        chosen = next((c.worker for c in cands if c.eligible), None)
        trace = net.excite(f"route:{args.capability}", capability=args.capability)
        data = {
            "capability": args.capability,
            "chosen": chosen,
            "population": trace.fired,
            "candidates": [c.__dict__ for c in cands],
        }
        text = f"chosen: {chosen}  (population: {trace})\n" + "\n".join(
            f"  {'*' if c.eligible else '-'} {c.worker}{'' if c.eligible else '  (' + str(c.reason) + ')'}" for c in cands
        )
        _out(data, args.json, text)
        return 0 if chosen else 1

    if args.cmd == "run":
        card = _card(args)
        env = run_card(reg, card, args.worker, _opts(args))
        if not _isolated_test_env():
            trace = net.excite(card.goal[:120], capability=card.capability, respect_cooldowns=True)
            net.learn(trace, success=env.ok)
            net.save_weights()
        _out(env.to_dict(), args.json, _envelope_text(env))
        return 0 if env.ok else 1

    if args.cmd == "batch":
        all_ok = True
        for env in batch(reg, load_jsonl(args.file), parallel=args.parallel, worker=args.worker or "auto", opts=_opts(args)):
            all_ok &= env.ok
            print(json.dumps(env.to_dict(), ensure_ascii=False, default=str) if args.json else _envelope_text(env))
            sys.stdout.flush()
        return 0 if all_ok else 1

    if args.cmd == "ask" and args.many:
        lines = Path(args.many).read_text(encoding="utf-8").splitlines()
        prompts = [json.loads(line)["prompt"] for line in lines if line.strip()]
        rows = ask_many(reg, args.tier, prompts, system=args.system, timeout_s=args.timeout)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 0 if all("text" in r for r in rows) else 1

    if args.cmd == "ask":
        prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
        if not prompt:
            raise FleetError("give a prompt or --prompt-file")
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8")) if args.schema else None
        if args.stream:
            for delta in ask_stream(reg, args.tier, prompt, system=args.system, timeout_s=args.timeout, private=args.private):
                sys.stdout.write(delta)
                sys.stdout.flush()
            print()
            return 0
        if args.vote:
            r = vote(
                reg,
                args.tier,
                prompt,
                system=args.system,
                files=args.file,
                max_tokens=args.max_tokens,
                timeout_s=args.timeout,
            )
            _out(r.to_dict(), args.json, f"{r.data['answer']}  ({r.data['agree']}/{r.data['of']} agree: {r.tier})")
            return 0
        r = ask(
            reg,
            args.tier,
            prompt,
            system=args.system,
            schema=schema,
            files=args.file,
            images=args.image,
            max_tokens=args.max_tokens,
            timeout_s=args.timeout,
            private=args.private,
        )
        _out(r.to_dict(), args.json, r.text if r.data is None else json.dumps(r.data, indent=2, ensure_ascii=False))
        return 0 if r.valid is not False else 1

    if args.cmd == "ledger":
        if args.usage:
            rows = [{"name": k, **v} for k, v in ledger.usage().items()]
            _out(rows, args.json, _table(rows, ["name", "today", "7d", "30d", "failed_30d", "quota_30d"]))
            return 0
        data = ledger.summary() if args.summary else ledger.read(args.tail)
        _out(data, True)
        return 0

    if args.cmd == "eval":
        tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
        only = args.only.split(",") if args.only else None
        rows = evaluate(reg, tiers, only=only, timeout_s=args.timeout)
        table_rows = [{**r, "score": f"{r['score']}/{r['n']}", "misses": "; ".join(r["misses"])[:120]} for r in rows]
        _out(rows, args.json, _table(table_rows, ["tier", "model", "score", "median_s", "misses"]))
        return 0

    if args.cmd == "providers":
        from .doctor import provider_rows

        rows = provider_rows(reg)
        cols = ["id", "provider", "model", "cost", "adapter", "key_env", "key_present", "cooldown_until"]
        _out(rows, args.json, _table(rows, cols))
        return 0

    if args.cmd == "slots":
        from . import slots

        if args.refresh:
            print(json.dumps(slots.refresh_models(reg)), file=sys.stderr)
        rows = slots.provider_slots(reg)
        if args.models:
            row = next((r for r in rows if r["provider"] == args.models), None)
            if row is None:
                raise FleetError(f"unknown provider {args.models!r}; known: {', '.join(r['provider'] for r in rows)}")
            _out(row["models"], args.json, "\n".join(row["models"]))
            return 0
        table = [{**r, "tiers": ",".join(r["tiers"])[:70], "cooled": ",".join(r["cooled"])} for r in rows]
        harness = slots.harness_slots(reg)
        _out(
            {"providers": rows, "harnesses": harness},
            args.json,
            _table(table, ["provider", "via", "cost", "slots", "key_present", "n_models", "tiers", "cooled"])
            + "\n\n"
            + _table(harness, ["harness", "via", "cost", "slots", "state", "jobs"]),
        )
        return 0

    if args.cmd == "jobs":
        from . import slots

        jobs = slots.load_jobs()
        rows = [
            {
                "job": k,
                "mode": j.get("mode", "ask"),
                "models": ",".join(j.get("tiers") or [j.get("worker") or f"workers:{j.get('capability')}"]),
                "note": j.get("note", "")[:70],
            }
            for k, j in jobs.items()
        ]
        bad = slots.check_jobs(reg)
        _out(
            {"jobs": jobs, "errors": bad},
            args.json,
            _table(rows, ["job", "mode", "models", "note"]) + "".join(f"\nCONFIG ERROR {b}" for b in bad),
        )
        return 1 if bad else 0

    if args.cmd == "job":
        from . import slots

        prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
        if not prompt:
            raise FleetError("give a prompt or --prompt-file")
        data = slots.run_job(
            reg,
            args.kind,
            prompt,
            cwd=args.cwd,
            done_when=args.done_when,
            system=args.system,
            files=args.file,
            images=args.image,
            timeout_s=args.timeout,
            allow_claude=args.allow_claude,
        )
        _out(data, args.json, f"[{data['job']} -> {data['by']}: {data['status']}]\n{data.get('text') or data.get('reason')}")
        return 0 if data["status"] == "ok" else 1

    if args.cmd == "qc":
        from . import slots

        data = slots.qc_images(reg, args.root, since_min=args.since, limit=args.limit, subject=args.subject)
        lines = [f"{'PASS' if r['pass'] else 'FAIL'} {r['file']}\n     {r['note']}" for r in data["results"]]
        _out(data, args.json, "\n".join(lines) or data["reason"])
        return 0 if data["ok"] else 1

    if args.cmd == "daemon":
        from . import slots

        slots.daemon(args.interval, once=args.once)
        return 0

    if args.cmd == "bakeoff":
        from .evals import apply_scores

        tiers = [x.strip() for x in args.tiers.split(",") if x.strip()]
        rows = evaluate(reg, tiers, only=args.only.split(",") if args.only else None, timeout_s=args.timeout)
        changed = apply_scores(net, rows) if args.apply else []
        if args.apply and changed:
            net.save_weights()
        data = {"rows": rows, "applied": changed}
        table_rows = [{**r, "score": f"{r['score']}/{r['n']}", "misses": "; ".join(r["misses"])[:100]} for r in rows]
        _out(
            data,
            args.json,
            _table(table_rows, ["tier", "model", "score", "median_s", "misses"])
            + (f"\napplied to {len(changed)} synapses" if args.apply else ""),
        )
        return 0

    if args.cmd == "spend":
        from .costs import modulation, spend_today

        per = spend_today(reg)
        data = {
            "per_tier_usd": per,
            "total_usd": round(sum(per.values()), 6),
            "budget_usd": args.budget,
            "modulation": round(modulation(reg, args.budget), 3),
        }
        rows = [{"tier": k, "usd": v} for k, v in sorted(per.items(), key=lambda kv: -kv[1])]
        _out(
            data,
            args.json,
            _table(rows, ["tier", "usd"])
            + f"\ntotal ${data['total_usd']} of ${args.budget} budget; learn modulation x{data['modulation']}",
        )
        return 0

    if args.cmd == "remember":
        from .memory_embed import remember

        data = remember(reg, args.text, args.tier)
        _out(data, args.json, f"stored ({data['dims']} dims): {data['stored'][:80]}")
        return 0

    if args.cmd == "recall":
        from .memory_embed import recall

        rows = recall(reg, args.query, k=args.k, tier_spec=args.tier)
        _out(rows, args.json, _table(rows, ["score", "text"]))
        return 0

    if args.cmd == "weights":
        if args.action == "export":
            net.save_weights(args.path)  # the live weights: shipped seed + what this machine learned
            _out({"exported": args.path}, args.json, f"weights exported to {args.path}")
            return 0
        other = json.loads(Path(args.path).read_text(encoding="utf-8"))
        data = net.merge_weights(other)
        _out(data, args.json, f"merged {len(data['merged'])} edges, adopted {len(data['added'])} new edges; weights saved")
        return 0

    if args.cmd == "board":
        board = Board(reg, args.dir)
        if args.bcmd == "init":
            print(board.ensure())
            return 0
        if args.bcmd == "add":
            card = _card(args)
            if args.worker:
                card.worker = args.worker
            issue = board.add(card, deps=args.dep, priority=args.priority, hold=args.hold)
            _out({"id": issue}, args.json, issue)
            return 0
        if args.bcmd == "release":
            board.release(args.id)
            print(f"released {args.id}")
            return 0
        if args.bcmd in ("ready", "list"):
            rows = board.ready() if args.bcmd == "ready" else board.list(args.status)
            slim = [
                {
                    "id": i.get("id"),
                    "title": i.get("title"),
                    "status": i.get("status"),
                    "priority": i.get("priority"),
                    "labels": ",".join(i.get("labels") or []),
                }
                for i in rows
            ]
            _out(slim, args.json, _table(slim, ["id", "status", "priority", "title", "labels"]) if slim else "(none)")
            return 0
        if args.bcmd == "show":
            _out(board.get(args.id), True)
            return 0

    if args.cmd == "work":
        rows = work(
            reg,
            Board(reg, args.dir),
            max_tasks=args.max_tasks,
            max_minutes=args.max_minutes,
            actor=args.actor,
            worker=args.worker or "auto",
            opts=_opts(args),
            on_result=lambda r: print(json.dumps(r, ensure_ascii=False), flush=True),
        )
        print(json.dumps({"processed": len(rows), "ok": sum(r["status"] == "ok" for r in rows)}))
        return 0 if all(r["status"] == "ok" for r in rows) else 1

    if args.cmd == "worktree":
        from . import worktree

        data = worktree.list_worktrees(args.cwd) if args.action == "list" else worktree.prune_merged(args.cwd)
        _out(data, True)
        return 0

    if args.cmd == "leader":
        if args.lcmd == "elect":
            data = leader.elect_leader(reg)
            lines = [f"elected: {data['elected']} ({data['model'] or '-'})"] + [
                f"  {'*' if c['eligible'] else '-'} {c['tier']} ({c['model']}): {c['reason']}" for c in data["candidates"]
            ]
            _out(data, args.json, "\n".join(lines))
            return 0 if data["elected"] else 1
        if args.lcmd == "wake":
            data = leader.wake_leader(
                args.report,
                system=args.system,
                channel=args.channel,
                repo=args.repo,
                issue=args.issue,
                reg=reg,
            )
            _out(data, args.json, f"[{data['used_tier']} / {data['model']}]\n{data['reply']}")
            return 0
        if args.lcmd == "comm":
            data = leader.comm_message(
                args.to,
                args.message,
                repo=args.repo,
                issue=args.issue,
                title=args.title,
                labels=args.label,
                read=args.read,
                channel=args.channel,
                reg=reg,
            )
            text = data.get("reply") or data.get("url") or data.get("output")
            _out(data, args.json, text)
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
