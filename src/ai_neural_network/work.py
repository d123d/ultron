"""Work loop: drain ready board tasks (claim, run, verify, close or reopen) and batch fan-out."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace

from .board import Board
from .card import TaskCard
from .envelope import Envelope
from .registry import Registry
from .runner import RunOptions, run_card

MAX_FAILS = 3


def _fail_count(issue: dict) -> int:
    n = 0
    for label in issue.get("labels") or []:
        if label.startswith("fleet:failed-"):
            n = max(n, int(label.rsplit("-", 1)[1]))
    return n


def work(reg: Registry, board: Board, *, max_tasks: int = 1, max_minutes: float | None = None,
         actor: str | None = None, worker: str = "auto", opts: RunOptions | None = None,
         on_result: Callable[[dict], None] | None = None) -> list[dict]:
    actor = actor or f"fleet-{socket.gethostname()}-{os.getpid()}"
    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
    done: list[dict] = []
    while len(done) < max_tasks and (deadline is None or time.monotonic() < deadline):
        ready = [i for i in board.ready() if "fleet:needs-operator" not in (i.get("labels") or [])]
        ready.sort(key=lambda i: (i.get("priority", 2), i.get("created_at", "")))
        issue = next((i for i in ready if board.claim(i["id"], actor)), None)
        if issue is None:
            break
        card = Board.card_of(board.get(issue["id"]))
        # a card that failed its checks before starts with the next harness, not the one that just failed it
        retry_opts = replace(opts or RunOptions(), rotate=_fail_count(issue))
        env = run_card(reg, card, card.worker or worker, retry_opts)
        row = {"task": issue["id"], "title": issue.get("title"), "status": env.status, "worker": env.worker,
               "run_id": env.run_id, "reason": env.reason}
        if env.ok:
            board.close(issue["id"], actor, f"{env.summary[:300]} (run {env.run_id})")
        else:
            n = _fail_count(issue) + 1
            label = "fleet:needs-operator" if n >= MAX_FAILS else f"fleet:failed-{n}"
            board.reopen(issue["id"], actor, f"{env.status}: {env.reason} (run {env.run_id})", label)
            row["label"] = label
        done.append(row)
        if on_result:
            on_result(row)
    return done


def batch(reg: Registry, cards: list[TaskCard], *, parallel: int = 2, worker: str = "auto",
          opts: RunOptions | None = None) -> Iterator[Envelope]:
    """Yield envelopes as runs complete, honouring each worker's max_parallel."""
    sems = {w.id: threading.Semaphore(w.max_parallel) for w in reg.workers.values()}
    guard = threading.Semaphore(max(1, parallel))

    def one(card: TaskCard) -> Envelope:
        wid = card.worker or worker
        sem = sems.get(wid)
        with guard:
            if sem:
                with sem:
                    return run_card(reg, card, wid, opts)
            return run_card(reg, card, wid, opts)

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = [pool.submit(one, c) for c in cards]
        for f in as_completed(futures):
            yield f.result()


def load_jsonl(path: str) -> list[TaskCard]:
    cards = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                cards.append(TaskCard(**json.loads(line)))
    return cards
