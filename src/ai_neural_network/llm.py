"""Direct LLM calls by tier (direct provider URLs, local Strata) with JSON-schema
validation and one repair turn.

Every request goes through the cross-process provider throttle (fleet.throttle) so parallel asks spread
across providers and no single provider gets too many requests too fast. Tiers in quota cooldown fail fast
instead of hammering the provider. A tier spec may be one tier, a comma list ("utility,main") or "auto"
(cheapest healthy tier first); on provider errors (429/5xx/unreachable) the next candidate tier is tried.
A single named tier also tries its registry `fallback` tiers, in order. Images go as OpenAI image_url parts.
"""
from __future__ import annotations

import base64
import http.client
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema

from . import health, ledger
from .paths import state_dir
from .procs import FleetError, redact
from .registry import Registry, Tier
from .throttle import Busy, provider_key, slot, spread_order

# Present while a timed Strata test runs; local tiers must stay quiet so they do not skew it.
STRATA_TEST_LOCK = Path(__file__).resolve().parents[4] / "state" / "strata-testing.lock"
STRATA_PORT = ":47831/"
TIER_COOLDOWN_S = 900  # back off this long after a provider says we are too hot
AUTH_COOLDOWN_S = 3600  # expired login or rejected key
AUTH_RE = re.compile(r"authenticat|oauth|not logged in|log ?in again|unauthori[sz]ed|invalid api key", re.I)
DOWN_COOLDOWN_S = 300  # unreachable, timed out or 5xx: skip the tier briefly instead of re-waiting the full timeout
_QUOTA_PATTERNS = ["rate limit", "rate_limit", "quota", "usage limit", "429", "insufficient_quota",
                   "exceeded your", "too many requests", "overloaded",
                   "balance is too low", "purchase more credits"]  # agy Google AI credits, codex plan credits


class ProviderError(FleetError):
    """Upstream provider failure worth trying on another tier: 4xx/5xx or unreachable/timeout."""

    def __init__(self, msg: str, status: int | None = None, retry_after_s: float | None = None, down: bool = False):
        super().__init__(msg)
        self.status, self.retry_after_s, self.down = status, retry_after_s, down  # down: unreachable or timed out


class CapReached(ProviderError):
    """The tier's daily_cap of calls (UTC day) is used up."""


def calls_today(tier_id: str) -> int:
    """Ledgered asks to this tier since UTC midnight (failed ones too: they cost quota as well)."""
    day = datetime.now(UTC).date().isoformat()
    return sum(1 for r in ledger.read() if r.get("type") == "ask" and r.get("tier") == tier_id
               and str(r.get("ts", "")).startswith(day))


def _check_cap(tier: Tier, n: int = 1) -> None:
    if tier.daily_cap and (used := calls_today(tier.id)) + n > tier.daily_cap:
        raise CapReached(f"tier {tier.id}: daily cap reached ({used}/{tier.daily_cap} calls today, UTC)")


class CutOff(ProviderError):
    """Reasoning used the whole max_tokens budget before any answer."""


@dataclass
class AskResult:
    text: str
    data: Any = None
    valid: bool | None = None
    model: str = ""
    tier: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _tier_key(tier: Tier) -> str:
    """Bearer key for a tier: ~/.secrets/.env (canonical), else env var `key_env`, else a dummy for keyless local tiers.

    The file wins: stale copies in Windows user env vars shadowed a renewed key (OPENCODE_API_KEY, 2026-11-06).
    """
    name = tier.key_env
    if not name:
        return os.environ.get("FLEET_LITELLM_KEY", "sk-fleet-local")
    try:
        for line in (Path.home() / ".secrets" / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                if value := line.split("=", 1)[1].strip().strip('"').strip("'"):
                    return value
    except OSError:
        pass
    if os.environ.get(name):
        return os.environ[name]
    raise FleetError(f"tier {tier.id}: key {name} not set (~/.secrets/.env or env)")


def _dropped(e: Exception) -> bool:
    """Connection lost mid-request (NIM vision dropped 1 of 3 on 2026-11-05). Refused or timed out: no retry."""
    cause = e.reason if isinstance(e, urllib.error.URLError) else e
    return isinstance(cause, (ConnectionError, http.client.HTTPException)) and not isinstance(cause, ConnectionRefusedError)


CLI_IMAGES = {"agy", "codex"}  # CLI routes that can look at image files; claude runs with no tools


def _flat(messages: list[dict]) -> str:
    one = len(messages) == 1 and messages[0]["role"] == "user"
    return messages[0]["content"] if one else "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages)


AGY_AGENT = "---\nname: fleet-ask\ndescription: Answers one question directly with no tools.\ntools: []\n---\n" \
            "Answer the user's question directly. Use no tools.\n"


def _agy_plain_agent(work: Path) -> list[str]:
    """A no-tools agy agent in the call's own folder: the prompt drops from ~13.8k to ~6.7k tokens (agy reads
    workspace agents from .agents/agents). Image asks keep the default agent, which can open files."""
    d = work / ".agents" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fleet-ask.md").write_text(AGY_AGENT, encoding="utf-8")
    return ["--agent", "fleet-ask"]


def _cli_argv(tier: Tier, text: str, work: Path, names: list[str]) -> tuple[list[str], str | None, str]:
    """argv, stdin text and prompt text for one subscription CLI print call."""
    from .adapters import _agy_prompt, _exe  # lazy: adapters pull in card/runner pieces

    if tier.cli == "agy":  # Google subscription: Gemini, Claude via Antigravity. Effort is part of the model id.
        if names:
            text += "\n\nAttached image files in the current folder (open each with view_file): " + ", ".join(names)
        args, stdin_text = _agy_prompt(text)
        argv = [_exe("agy"), *args, "--model", tier.model, "--disable-slash-commands"]
        return argv + ([] if names else _agy_plain_agent(work)), stdin_text, text
    if tier.cli == "codex":  # ChatGPT subscription; prompt on stdin ("-"), read-only sandbox, answer to a file
        argv = [_exe("codex"), "exec", "-", "-m", tier.model, "--skip-git-repo-check", "--ephemeral",
                "-s", "read-only", "-o", str(work / "answer.md")]
        if tier.effort:
            argv += ["-c", f"model_reasoning_effort={tier.effort}"]
        for n in names:
            argv += ["-i", n]
        return argv, text, text
    if tier.cli == "claude":  # Claude subscription; no tools, nothing saved
        argv = [_exe("claude"), "-p", "--output-format", "json", "--model", tier.model, "--tools", "",
                "--no-session-persistence"]
        # "[claude-code:unrecognized_model]" on stderr (e.g. claude-fable-5-2) is only a warning; the call runs
        if tier.effort:
            argv += ["--effort", tier.effort]
        return argv, text, text
    if tier.cli == "copilot":
        return [_exe("copilot"), "-p", text, "-s"], None, text
    raise FleetError(f"tier {tier.id}: unknown cli {tier.cli!r}")


def _retry_at_s(msg: str) -> float | None:
    """Seconds until Codex 'try again at ...' or Claude 'resets 3pm (TZ)' time, else None."""
    m = re.search(r"try again at (\w+) (\d+)(?:st|nd|rd|th)?, (\d{4}) (\d+:\d+ [AP]M)", msg)
    if m:
        try:
            at = datetime.strptime(" ".join(m.groups()), "%b %d %Y %I:%M %p").astimezone()  # local time
            return max(60.0, (at - datetime.now(UTC)).total_seconds())
        except ValueError:
            return None
    m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^)]+)\))?", msg, re.I)
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    minute = int(m.group(2) or 0)
    tz = None
    if tz_name := m.group(4):
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name.strip())
        except Exception:
            tz = None
    now_local = datetime.now(tz) if tz else datetime.now().astimezone()
    at = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= now_local:
        at += timedelta(days=1)
    return max(60.0, (at - datetime.now(UTC)).total_seconds() + 60.0)


def _cli_post(tier: Tier, messages: list[dict], timeout_s: float, images: list[str] | tuple = ()) -> dict:
    """Subscription tier: one CLI print call (agy, codex, claude or copilot), returned shaped like a chat completion."""
    from .adapters import Adapter, AgyAdapter, ClaudeAdapter

    work = state_dir() / "cli-ask" / uuid.uuid4().hex  # own empty dir: no repo context, no clashes between calls
    work.mkdir(parents=True)
    try:
        names = []
        for i, img in enumerate(images, 1):
            names.append(f"image{i}{Path(img).suffix.lower()}")
            shutil.copyfile(img, work / names[-1])
        argv, stdin_text, _ = _cli_argv(tier, _flat(messages), work, names)

        def run() -> subprocess.CompletedProcess:
            try:
                return subprocess.run(argv, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=timeout_s, input=stdin_text if stdin_text is not None else "")
            except subprocess.TimeoutExpired:
                raise ProviderError(f"tier {tier.id}: {tier.cli} gave no answer within {timeout_s:.0f}s", down=True) from None

        p = run()
        usage: dict[str, Any] = {}
        if tier.cli == "codex":
            out = work / "answer.md"
            reply = out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else ""
            lines = (p.stderr + "\n" + p.stdout).splitlines()
            # the ERROR line carries the reason ("You've hit your usage limit ... try again at ..."); a tail cut it off
            reason = next((ln.strip() for ln in lines if ln.startswith("ERROR")), "") or p.stderr.strip()[-300:]
            error = None if p.returncode == 0 and reply else (reason or f"exit {p.returncode}")
            if m := re.search(r"tokens used\s+([\d,]+)", p.stdout + p.stderr):
                usage["prompt_tokens"] = int(m.group(1).replace(",", ""))
        elif tier.cli == "copilot":
            r = Adapter().parse(p)
            reply, error = r.reply, r.error
        else:
            r = (AgyAdapter() if tier.cli == "agy" else ClaudeAdapter()).parse(p)
            if tier.cli == "agy" and not r.reply and not r.error:  # agy sometimes ends a turn with no text (4/16 on 11-06)
                r = AgyAdapter().parse(p := run())
            reply, error = r.reply, r.error
            usage = {"prompt_tokens": r.usage.get("input_tokens"), "completion_tokens": r.usage.get("output_tokens")}
        if error or not reply:
            msg = f"tier {tier.id}: {tier.cli} {redact(error or 'returned an empty reply')[:300]}"
            if AUTH_RE.search(error or ""):  # expired login: no use retrying until someone logs in again
                raise ProviderError(msg + f" (log in again: run {tier.cli} once)", down=True, retry_after_s=AUTH_COOLDOWN_S)
            raise ProviderError(msg, retry_after_s=_retry_at_s(error or ""))
        return {"model": tier.model, "choices": [{"message": {"content": reply}, "finish_reason": "stop"}], "usage": usage}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _post(reg: Registry, tier: Tier, messages: list[dict], *, max_tokens: int | None, json_mode: bool,
          timeout_s: float, images: list[str] | tuple = ()) -> dict:
    if tier.cli:
        with slot(reg, tier.base_url):
            return _cli_post(tier, messages, timeout_s, images)
    body: dict[str, Any] = {"model": tier.model, "messages": messages, "temperature": 0.2}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    key = _tier_key(tier)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if tier.session_header:
        # opencode.ai rejects chat completions without this (HTTP 400); the gate used to add it.
        headers["x-opencode-session"] = str(uuid.uuid4())
        # opencode.ai sits behind Cloudflare, which 403s urllib's default UA (error 1010);
        # the gate passed with an app UA, so send a browser one here.
        headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
    req = urllib.request.Request(f"{tier.base_url.rstrip('/')}/chat/completions", data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    for attempt in (1, 2):
        with slot(reg, tier.base_url):  # spacing + concurrency, cross-process
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = redact(e.read().decode("utf-8", "replace")[:400])
                retry_after: float | None = None
                if (ra := e.headers.get("Retry-After")) is not None:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        pass
                raise ProviderError(f"tier {tier.id} HTTP {e.code}: {detail}", status=e.code, retry_after_s=retry_after) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
                if attempt == 2 or not _dropped(e):
                    raise ProviderError(f"tier {tier.id} unreachable at {tier.base_url}: {e}", down=True) from None
    raise AssertionError("unreachable")


def _text(resp: dict) -> str:
    try:
        choice = resp["choices"][0]
        text = (choice["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        raise FleetError(f"unexpected completion shape: {str(resp)[:300]}") from None
    # An empty answer is never success: it was ledgered ok and blocked fallback to the next tier (2026-11-05/06).
    if not text:
        if choice.get("finish_reason") == "length":
            raise CutOff("reply cut off at max_tokens before any answer (reasoning used the budget); raise --max-tokens")
        raise ProviderError(f"empty answer (finish_reason {choice.get('finish_reason')!r}, e.g. all reasoning, no content)")
    return text


def _parse_json(text: str) -> Any:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON object in reply")
    end = max(t.rfind("}"), t.rfind("]"))
    return json.loads(t[start : end + 1])


def candidates(reg: Registry, tier_spec: str) -> list[str]:
    """Resolve a tier spec: single tier, comma list, or 'auto' (free/flat tiers, cheapest first)."""
    if tier_spec == "auto":
        # auto never spends money (metered/claude tiers only when named) and skips CLI tiers (~40 s a call, shared
        # subscription quota): free/flat HTTP tiers, free first.
        rank = {c: i for i, c in enumerate(reg.cost_order)}
        free = [t for t in reg.tiers if reg.tiers[t].cost in ("free", "flat") and not reg.tiers[t].cli]
        return sorted(free, key=lambda t: (rank.get(reg.tiers[t].cost, 99), t))
    ids = list(dict.fromkeys(s.strip() for s in tier_spec.split(",") if s.strip())) if "," in tier_spec else [tier_spec]
    unknown = [i for i in ids if i not in reg.tiers]
    if unknown:
        raise FleetError(f"unknown tier {unknown[0]!r}; known: {', '.join(reg.tiers)}")
    if "," not in tier_spec:  # a bare tier name brings its fallbacks; "main," means main alone
        ids += [f for f in reg.tiers[ids[0]].fallback if f not in ids]
    return ids


def local_tiers(reg: Registry) -> list[str]:
    """Tiers served on this machine (Strata): nothing leaves the PC."""
    # Strata's port only: other localhost services (the Zen bridge :4097) forward to the cloud
    return [t.id for t in reg.tiers.values() if not t.cli and STRATA_PORT in t.base_url + "/"]


def _not_in_strata_test(reg: Registry, ids: list[str]) -> list[str]:
    if not STRATA_TEST_LOCK.exists():
        return ids
    ok = [t for t in ids if STRATA_PORT not in reg.tiers[t].base_url + "/"]
    if not ok:
        raise FleetError("Strata is in a timed test (state/strata-testing.lock); pass a cloud tier or wait")
    return ok


def _not_in_cooldown(ids: list[str]) -> list[str]:
    blocked, ok = [], []
    for tid in ids:
        until = health.cooldown_until(tid)
        (blocked if until else ok).append(f"{tid} until {health.iso(until)}" if until else tid)
    if not ok:
        raise FleetError(f"all requested tiers are in cooldown ({'; '.join(blocked)}); wait or pass other tiers")
    return ok


def _is_quota_err(reg: Registry, err: ProviderError) -> bool:
    hay = str(err).lower()
    if err.status in (402, 429):  # 402 = prepaid credits depleted (Gemini 2026-11-06): cool down, don't hammer
        return True
    patterns = list(reg.quota_patterns) + _QUOTA_PATTERNS
    return any(p.lower() in hay for p in patterns)


def _ask_one(reg: Registry, tier_id: str, prompt: str, *, system: str | None = None, schema: dict | None = None,
             files: list[str] | tuple = (), images: list[str] | tuple = (), max_tokens: int | None = None,
             timeout_s: float = 180) -> AskResult:
    tier = reg.tiers[tier_id]
    _check_cap(tier)  # subscription quota is shared with people's own sessions: never burn it in a loop
    user = prompt
    for f in files:
        p = Path(f)
        user += f"\n\nFILE {p}:\n```\n{p.read_text(encoding='utf-8', errors='replace')}\n```"
    if schema is not None:
        user += "\n\nReturn only JSON matching this schema:\n" + json.dumps(schema)
    http_images = images if not tier.cli else ()  # CLI routes get the files themselves
    content: Any = [{"type": "text", "text": user}] + [_image_part(i) for i in http_images] if http_images else user
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    start = time.monotonic()
    status = "error"
    usage: dict[str, Any] = {}
    error = ""
    try:
        resp = _post(reg, tier, messages, max_tokens=max_tokens, json_mode=schema is not None, timeout_s=timeout_s,
                     images=images if tier.cli else ())
        try:
            text = _text(resp)
        except CutOff:  # reasoning ate the budget: one retry with room to spare before falling back
            max_tokens = max(2 * (max_tokens or 8192), 16384)
            resp = _post(reg, tier, messages, max_tokens=max_tokens, json_mode=schema is not None, timeout_s=timeout_s)
            text = _text(resp)
        usage = resp.get("usage") or {}
        result = AskResult(text=text, model=resp.get("model", tier.model), tier=tier_id,
                           provider=provider_key(tier.base_url))
        if schema is not None:
            for attempt in range(2):
                try:
                    data = _parse_json(text)
                    jsonschema.validate(data, schema)
                    result.data, result.valid = data, True
                    break
                except (ValueError, jsonschema.ValidationError) as e:
                    result.valid = False
                    if attempt:
                        break
                    msg = getattr(e, "message", str(e))
                    messages += [{"role": "assistant", "content": text},
                                 {"role": "user", "content": f"Your JSON failed validation: {msg}. Return corrected JSON only."}]
                    resp = _post(reg, tier, messages, max_tokens=max_tokens, json_mode=True, timeout_s=timeout_s)
                    text = _text(resp)
                    result.text = text
        result.usage = usage
        result.duration_s = round(time.monotonic() - start, 2)
        status = "ok" if result.valid is not False else "failed"
        if status == "failed":
            error = "reply failed schema validation after one repair"
        return result
    except Exception as e:
        error = redact(str(e))[:300]
        raise
    finally:
        row = {"type": "ask", "tier": tier_id, "cost": tier.cost, "status": status,
               "duration_s": round(time.monotonic() - start, 2),
               "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
        if error:  # ledger errors without a reason could not be diagnosed (2026-11-06)
            row["error"] = error
        ledger.append(row)


def _image_part(path: str) -> dict:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or ""
    if not mime.startswith("image/"):
        raise FleetError(f"not an image file: {p}")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"}}


def _free_daily_reset_s(tier: Tier) -> int | None:
    """Seconds to the UTC-midnight reset when OpenRouter's shared free-model daily quota is spent, else None."""
    if "openrouter.ai" not in tier.base_url or not tier.model.endswith(":free"):
        return None
    try:
        req = urllib.request.Request(f"{tier.base_url.rstrip('/')}/key", headers={"Authorization": f"Bearer {_tier_key(tier)}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            left = json.loads(r.read())["data"]["free_model_daily_requests"]["remaining"]
    except (OSError, ValueError, KeyError, TypeError, FleetError):
        return None  # unknown: fall back to the normal cooldown
    if left > 0:
        return None
    now = datetime.now(UTC)
    return int((now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - now).total_seconds()) + 60


def ask(reg: Registry, tier_spec: str, prompt: str, *, system: str | None = None, schema: dict | None = None,
        files: list[str] | tuple = (), images: list[str] | tuple = (), max_tokens: int | None = None,
        timeout_s: float = 180, private: bool = False) -> AskResult:
    """Call an LLM tier. tier_spec: one tier (+ its fallback tiers), 'auto' (cheapest healthy), or a comma list.
    private=True: only tiers on this machine, whatever tier_spec says."""
    cands = local_tiers(reg) if private else candidates(reg, tier_spec)
    if private and not cands:
        raise FleetError("no local tier in the registry for a private ask")
    if images:  # claude CLI runs with no tools: cannot open an image
        cands = [t for t in cands if reg.tiers[t].cli in ("", *CLI_IMAGES)]
        if not cands:
            raise FleetError(f"no image-capable tier in {tier_spec!r}; use --tier vision")
    cands = _not_in_cooldown(_not_in_strata_test(reg, cands))
    named = tier_spec != "auto" and "," not in tier_spec  # keep the primary-then-fallback order
    last: ProviderError | None = None
    down: set[str] = set()  # providers that failed to answer at all during this call
    for tid in (cands if named else spread_order(reg, cands)):
        if health.in_cooldown(tid):  # a sibling on the same shared quota cooled it down this call
            continue
        if provider_key(reg.tiers[tid].base_url) in down:  # same provider just timed out: try another first
            continue
        try:
            return _ask_one(reg, tid, prompt, system=system, schema=schema, files=files, images=images,
                            max_tokens=max_tokens, timeout_s=timeout_s)
        except (Busy, CapReached) as e:  # provider queue full or daily cap used: next tier, no cooldown
            last = e if isinstance(e, ProviderError) else ProviderError(str(e))
            if len(cands) == 1:
                raise
            continue
        except ProviderError as e:
            last = e
            if _is_quota_err(reg, e):
                tier = reg.tiers[tid]
                if daily := _free_daily_reset_s(tier):
                    # every :free model on the key shares one daily quota: park them all until the reset
                    for sib in reg.tiers.values():
                        if sib.base_url == tier.base_url and sib.model.endswith(":free"):
                            health.set_cooldown(sib.id, daily, "OpenRouter free-model daily quota spent")
                else:
                    wait = e.retry_after_s if e.retry_after_s else TIER_COOLDOWN_S
                    health.set_cooldown(tid, int(wait), f"provider too hot: {str(e)[:200]}")
                health.record_error(tid, str(e))
            elif e.down or (e.status or 0) >= 500 or e.status in (401, 403):
                wait = AUTH_COOLDOWN_S if e.status in (401, 403) else int(e.retry_after_s or DOWN_COOLDOWN_S)
                health.set_cooldown(tid, wait, f"provider down or login/key rejected: {str(e)[:200]}")
                health.record_error(tid, str(e))
                if e.down:
                    down.add(provider_key(reg.tiers[tid].base_url))
            if len(cands) == 1:
                raise
    raise last if last else FleetError(f"no usable tier in {tier_spec!r}")


def _final(text: str) -> str:
    """Short answer for voting: the last 'ANSWER:' line, else the last line; case, spaces and end punctuation ignored."""
    m = re.findall(r"ANSWER:\s*(.+)", text)
    line = m[-1] if m else (text.strip().splitlines() or [""])[-1]
    return " ".join(line.strip().strip("*`.").lower().split())


def vote(reg: Registry, tier_spec: str, prompt: str, *, system: str | None = None, files: list[str] | tuple = (),
         max_tokens: int | None = None, timeout_s: float = 180) -> AskResult:
    """Ask every tier in a comma list at once (no fallbacks) and return the majority short answer."""
    tiers = _not_in_cooldown(_not_in_strata_test(reg, candidates(reg, tier_spec + ",")))
    if len(tiers) < 2:
        raise FleetError("vote needs at least 2 usable tiers, e.g. --tier main,nim-glm,nim-minimax")
    sys_ = ((system + "\n\n") if system else "") + "End with a final line exactly 'ANSWER: <short answer>'."

    def one(tid: str) -> AskResult | None:
        try:
            return ask(reg, tid + ",", prompt, system=sys_, files=files, max_tokens=max_tokens, timeout_s=timeout_s)
        except FleetError:
            return None

    with ThreadPoolExecutor(len(tiers)) as ex:
        got = [r for r in ex.map(one, tiers) if r]
    if not got:
        raise FleetError(f"every voter failed in {tier_spec!r}")
    groups: dict[str, list[AskResult]] = {}
    for r in got:
        groups.setdefault(_final(r.text), []).append(r)
    answer, winners = max(groups.items(), key=lambda kv: len(kv[1]))
    best = winners[0]
    best.tier = ",".join(r.tier for r in winners)
    best.data = {"answer": answer, "agree": len(winners), "of": len(got),
                 "votes": {a: [r.tier for r in rs] for a, rs in groups.items()}}
    return best


def ask_many(reg: Registry, tier_spec: str, prompts: list[str], *, system: str | None = None,
             timeout_s: float = 180) -> list[dict[str, Any]]:
    """Many independent prompts. An agy tier answers them all in one process (one turn each, ~20 s a turn
    instead of ~40 s a call); every other tier gets one ask per prompt."""
    tier = reg.tiers.get(tier_spec)
    if tier is None or tier.cli != "agy":
        out = []
        for p in prompts:
            try:
                r = ask(reg, tier_spec, p, system=system, timeout_s=timeout_s)
                out.append({"tier": r.tier, "text": r.text})
            except FleetError as e:
                out.append({"tier": tier_spec, "error": str(e)[:300]})
        return out
    from .adapters import _exe

    _check_cap(tier, len(prompts))
    head = (system + "\n\n") if system else ""
    lines = [json.dumps({"event": "user", "message": {"role": "user", "content":
             f"{head}Independent question {i} of {len(prompts)}; ignore earlier questions.\n\n{p}"}})
             for i, p in enumerate(prompts, 1)]
    work = state_dir() / "cli-ask" / uuid.uuid4().hex
    work.mkdir(parents=True)
    start = time.monotonic()
    answers: list[dict[str, Any]] = []
    try:
        with slot(reg, tier.base_url):
            p = subprocess.run([_exe("agy"), "--input-format", "stream-json", "--output-format", "stream-json",
                                "--model", tier.model, "--disable-slash-commands", *_agy_plain_agent(work), "-p="],
                               cwd=work, input="\n".join(lines),
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout_s * len(prompts))
        for line in p.stdout.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("event") == "result":
                res = msg["result"]
                ok = res.get("status") == "SUCCESS" and (res.get("response") or "").strip()
                answers.append({"tier": tier.id, "text": res["response"].strip()} if ok else
                               {"tier": tier.id, "error": redact(str(res.get("error") or "empty reply"))[:300]})
    except subprocess.TimeoutExpired:
        pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    answers += [{"tier": tier.id, "error": "no answer (agy stopped early)"}] * (len(prompts) - len(answers))
    each = round((time.monotonic() - start) / max(1, len(prompts)), 2)
    for a in answers:
        ledger.append({"type": "ask", "tier": tier.id, "cost": tier.cost, "status": "ok" if "text" in a else "error",
                       "duration_s": each, "batch": len(prompts), **({"error": a["error"]} if "error" in a else {})})
    return answers
