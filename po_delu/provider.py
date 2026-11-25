"""OpenAI-compatible providers. No endpoint is contacted on import."""

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

SLOP_REV = "94060716b06d2e8c1e21018b994033f014d23e7d"
SLOP_SHA = "d693f385cbd9e9a2636850dd8d93e39216d7b3b50d3b6d80457d1fe91e961fcc"


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def slopotron():
    content = (Path(__file__).parent / "vendor" / "slopotron" / "SKILL.md").read_bytes()
    if digest(content) != SLOP_SHA:
        raise ValueError("Контрольная сумма инструкции Слопотрона не совпадает")
    return content.decode()


def family(model):
    # Classify the base weights, including common distilled and quantized variants.
    name = model.casefold().split(":")[0].split("/")[-1]
    for lineage, pattern in [
        ("qwen", "qwen|qwq"),
        ("llama", "llama"),
        ("gemma", "gemma"),
        ("gemini", "gemini"),
        ("claude", "claude"),
        ("gpt", r"gpt|^o[134](?:-|$)"),
        ("deepseek", "deepseek"),
        ("mistral", "mistral|mixtral|ministral|codestral|devstral"),
        ("glm", "glm"),
        ("grok", "grok"),
        ("kimi", "kimi"),
        ("phi", r"\bphi-"),
        ("command-r", "command-r"),
        ("smollm", "smollm"),
        ("granite", "granite"),
    ]:
        if re.search(pattern, name):
            return lineage
    return None


@dataclass(frozen=True)
class Endpoint:
    model: str
    base: str
    key: str = field(repr=False)

    @property
    def configured(self):
        host = urlsplit(self.base).hostname
        approved = {x.strip() for x in os.getenv("EDITOR_KEYLESS_HOSTS", "").split(",") if x.strip()}
        return bool(self.model and (self.key or host in {"localhost", "127.0.0.1", "::1"} | approved))

    @classmethod
    def role(cls, role=""):
        def setting(field):
            return os.getenv(f"EDITOR_{role}_{field}") if role else None

        base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        override = setting("BASE_URL")
        inherited_key = (
            os.getenv("LLM_API_KEY", "")
            if not override or override.rstrip("/") == base.rstrip("/")
            else ""
        )
        return cls(
            setting("MODEL") or os.getenv("LLM_MODEL", ""),
            (override or base).rstrip("/"),
            setting("API_KEY") or inherited_key,
        )


def judges():
    specs = json.loads(os.getenv("EDITOR_JUDGES_JSON") or "[]")
    if not isinstance(specs, list) or len(specs) > 12:
        raise ValueError("EDITOR_JUDGES_JSON должен содержать список до 12 моделей")
    base = Endpoint.role()
    panel, skipped, used = [], [], set()
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("model"), str):
            raise ValueError("Для судьи требуется идентификатор модели")
        model = spec["model"]
        lineage = family(model)
        if not lineage or lineage in used:
            skipped.append(
                {"model": model, "reason": "duplicate_family" if lineage else "unknown_family"}
            )
            continue
        used.add(lineage)
        url = str(spec.get("base_url", base.base)).rstrip("/")
        key = (
            os.getenv(spec["api_key_env"], "")
            if spec.get("api_key_env")
            else (base.key if url == base.base else "")
        )
        panel.append(Endpoint(model, url, key))
    return panel, skipped


class ProviderError(Exception):
    """A sanitized failure already recorded with the affected call."""


class Client:
    def __init__(self, http, stages, revision):
        self.http, self.stages, self.revision = http, stages, revision

    async def chat(self, endpoint, stage, messages, region, **options):
        start = time.monotonic()
        entry = dict(
            stage=stage,
            model=endpoint.model,
            family=family(endpoint.model),
            endpoint=urlsplit(endpoint.base).hostname,
            revision_hash=self.revision,
            region=region,
            input_hash=digest(messages),
            prompt_hash=digest(messages[0]["content"]),
            config_hash=digest(
                dict(
                    model=endpoint.model,
                    base=endpoint.base,
                    temperature=0,
                    options=options,
                    system=messages[0]["content"],
                )
            ),
            temperature=0,
            status="error",
            usage=None,
            cost_usd=None,
        )
        self.stages.append(entry)
        ledger = os.getenv("PO_DELU_BUDGET_DB")
        try:
            if not endpoint.configured:
                entry["error"] = "endpoint_not_configured"
                raise ProviderError(entry["error"])
            path = endpoint.base + "/chat/completions"
            request = dict(
                headers={"Authorization": "Bearer " + endpoint.key} if endpoint.key else {},
                json=dict(model=endpoint.model, messages=messages, temperature=0, **options),
                follow_redirects=False,
            )
            if ledger and endpoint.base.rstrip("/") == "https://openrouter.ai/api/v1":
                from spending import BudgetTransport

                tag = "interactive/" + stage
                entry["budget_tag"] = tag
                async with httpx.AsyncClient(
                    transport=BudgetTransport(ledger, tag), timeout=90
                ) as budgeted:
                    response = await budgeted.post(path, **request)
            elif ledger and urlsplit(endpoint.base).hostname not in ("localhost", "127.0.0.1", "::1"):
                entry["error"] = "endpoint_outside_budget"
                raise ProviderError(entry["error"])
            else:
                response = await self.http.post(path, **request)
            response.raise_for_status()
            body = response.json()
            usage = body.get("usage")
            if isinstance(usage, dict):
                entry["usage"] = usage
                entry["cost_usd"] = usage.get("cost")
            entry["served_model"] = body.get("model")
            entry["system_fingerprint"] = body.get("system_fingerprint")
            entry["finish_reason"] = body["choices"][0].get("finish_reason")
            msg = body["choices"][0]["message"]
            if not isinstance(msg, dict):
                raise ValueError("missing_message")
            entry["status"] = "ok"
            return msg, entry
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            ImportError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ) as exc:
            error = (
                f"http_{exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else type(exc).__name__
            )
            if ledger and isinstance(exc, ValueError):
                error = {
                    "Project spending limit reached": "project_budget_exhausted",
                    "Model or route outside the configured budget policy": "budget_model_not_allowed",
                }.get(str(exc), error)
            if isinstance(exc, sqlite3.Error):
                error = "budget_ledger_error"
            entry["error"] = error
            raise ProviderError(error) from None
        finally:
            entry["elapsed"] = round(time.monotonic() - start, 3)

    async def structured(self, endpoint, stage, system, payload, region):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        msg, entry = await self.chat(endpoint, stage, messages, region)
        try:
            answer = msg["content"].strip()
            if answer.startswith("```") and answer.endswith("```"):
                answer = "\n".join(answer.splitlines()[1:-1])
            value = json.loads(answer)
            if not isinstance(value, dict):
                raise ValueError()
            return value, entry
        except (AttributeError, KeyError, TypeError, ValueError):
            entry.update(status="error", error="invalid_json")
            raise ProviderError("invalid_json") from None
