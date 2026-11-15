"""Adapters for cloud providers whose APIs are not plain OpenAI-bearer chat completions.

`build(tier, body, secret, secret_opt) -> (url, headers, payload, parse_mode)`
`parse(adapter, data, mode) -> dict shaped like an OpenAI chat completion`

secret(name) returns a required secret or raises; secret_opt(name) returns one or "".
Stdlib only: Bedrock is SigV4-signed by hand, Vertex uses an API key (express mode)
or an OAuth token (GOOGLE_ACCESS_TOKEN / `gcloud auth print-access-token`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

Secret = Callable[[str], str]

ADAPTERS = ("azure", "bedrock", "vertex", "cloudflare")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(*, method: str, host: str, path: str, body: bytes, access: str, secret_key: str,
                  region: str, service: str, token: str = "", when: datetime | None = None) -> dict[str, str]:
    """AWS Signature Version 4 headers for one request (canonical URI = path as given)."""
    when = when or datetime.now(UTC)
    amz_date, date = when.strftime("%Y%m%dT%H%M%SZ"), when.strftime("%Y%m%d")
    signed = ["host", "x-amz-date"] + (["x-amz-security-token"] if token else [])
    canon_headers = f"host:{host}\nx-amz-date:{amz_date}\n" + (f"x-amz-security-token:{token}\n" if token else "")
    payload_hash = hashlib.sha256(body).hexdigest()
    canon = "\n".join([method, path, "", canon_headers, ";".join(signed), payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret_key).encode(), date)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={';'.join(signed)}, Signature={sig}"
    headers = {"Authorization": auth, "x-amz-date": amz_date, "Content-Type": "application/json"}
    if token:
        headers["x-amz-security-token"] = token
    return headers


def _flat_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def build(tier, body: dict[str, Any], secret: Secret, secret_opt: Secret) -> tuple[str, dict[str, str], dict[str, Any], str]:
    a = tier.adapter
    if a == "azure":
        endpoint = secret("AZURE_OPENAI_ENDPOINT").rstrip("/")
        version = secret_opt("AZURE_OPENAI_API_VERSION") or "2024-10-21"
        url = f"{endpoint}/openai/deployments/{tier.model}/chat/completions?api-version={version}"
        payload = {k: v for k, v in body.items() if k != "model"}  # deployment name is in the URL
        return url, {"api-key": secret(tier.key_env), "Content-Type": "application/json"}, payload, a
    if a == "bedrock":
        region = secret_opt("AWS_REGION") or "us-east-1"
        host = f"bedrock-runtime.{region}.amazonaws.com"
        path = f"/model/{quote(tier.model, safe='')}/converse"
        system = [m for m in body["messages"] if m["role"] == "system"]
        msgs = [{"role": m["role"], "content": [{"text": _flat_text(m["content"])}]}
                for m in body["messages"] if m["role"] in ("user", "assistant")]
        payload: dict[str, Any] = {"messages": msgs,
                                   "inferenceConfig": {"temperature": body.get("temperature", 0.2),
                                                       "maxTokens": body.get("max_tokens") or 4096}}
        if system:
            payload["system"] = [{"text": _flat_text(system[0]["content"])}]
        raw = json.dumps(payload).encode()
        headers = sigv4_headers(method="POST", host=host, path=path, body=raw, access=secret(tier.key_env),
                                secret_key=secret("AWS_SECRET_ACCESS_KEY"), region=region, service="bedrock",
                                token=secret_opt("AWS_SESSION_TOKEN"))
        return f"https://{host}{path}", headers, payload, a
    if a == "vertex":
        token = secret_opt("GOOGLE_ACCESS_TOKEN")
        if not token and shutil.which("gcloud"):
            try:
                token = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True,
                                       timeout=20).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                token = ""
        if token:
            project, loc = secret("VERTEX_PROJECT"), secret_opt("VERTEX_LOCATION") or "us-central1"
            url = f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/{loc}/endpoints/openapi/chat/completions"
            return url, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, body, "vertex-openai"
        url = f"https://aiplatform.googleapis.com/v1beta1/publishers/google/models/{tier.model}:generateContent"
        contents, system = [], []
        for m in body["messages"]:
            if m["role"] == "system":
                system.append({"parts": [{"text": _flat_text(m["content"])}]})
            else:
                contents.append({"role": "model" if m["role"] == "assistant" else "user",
                                 "parts": [{"text": _flat_text(m["content"])}]})
        payload = {"contents": contents,
                   "generationConfig": {"temperature": body.get("temperature", 0.2),
                                        **({"maxOutputTokens": body["max_tokens"]} if body.get("max_tokens") else {})}}
        if system:
            payload["systemInstruction"] = system[0]
        return url, {"x-goog-api-key": secret(tier.key_env), "Content-Type": "application/json"}, payload, "vertex-gemini"
    if a == "cloudflare":
        account = secret("CLOUDFLARE_ACCOUNT_ID")
        url = tier.base_url.replace("{account}", account).rstrip("/") + "/chat/completions"
        return url, {"Authorization": f"Bearer {secret(tier.key_env)}", "Content-Type": "application/json"}, body, a
    raise ValueError(f"unknown adapter {a!r}")


def parse(adapter: str, data: dict[str, Any], mode: str = "") -> dict[str, Any]:
    if adapter == "bedrock":
        parts = (data.get("output", {}).get("message", {}) or {}).get("content", [])
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = data.get("usage", {})
        return {"choices": [{"message": {"content": text},
                             "finish_reason": "length" if data.get("stopReason") == "max_tokens" else "stop"}],
                "usage": {"prompt_tokens": usage.get("inputTokens"), "completion_tokens": usage.get("outputTokens")}}
    if adapter == "vertex" and mode == "vertex-gemini":
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if isinstance(p, dict))
        usage = data.get("usageMetadata", {})
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": usage.get("promptTokenCount"),
                          "completion_tokens": usage.get("candidatesTokenCount")}}
    return data  # azure, cloudflare, vertex-openai already speak OpenAI format
