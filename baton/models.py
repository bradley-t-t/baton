"""Model "auto": the provider's newest flagship model.

With a key, the provider's model list is read and the newest model in its flagship
line is used. The answer is cached for a day in ~/.local/state/baton/models.json.
Without a list (no network, an error, an unexpected answer) the fallback below is used.

Fallbacks checked against each provider's docs on 2026-10-27:
  xAI        grok-4.7         https://docs.x.ai/docs/models
  OpenAI     gpt-6-astra      https://platform.openai.com/docs/models
  Anthropic  claude-opus-5-5  https://docs.anthropic.com/en/docs/about-claude/models/overview
"""

from __future__ import annotations

import json
import os
import re
import time

AUTO = "auto"

FALLBACK = {"xai": "grok-4.7", "openai": "gpt-6-astra", "anthropic": "claude-opus-5-5"}

# Defaults shipped before "auto". A config still on one of these was never chosen by
# hand, so setup moves it to "auto".
OLD_DEFAULTS = {"xai": {"grok-4"}, "openai": {"gpt-5"}, "anthropic": {"claude-sonnet-4-5"}}

# Flagship lines. xAI: plain grok-N or grok-N.M (no -mini, -fast, dated or
# reasoning variants). OpenAI: the Astra line, gpt-N[.M]-astra. Anthropic: line "opus"
# from the Models API, which says not to infer the line from the id.
PATTERNS = {"xai": re.compile(r"^grok-\d+(\.\d+)?$"),
            "openai": re.compile(r"^gpt-\d+(\.\d+)?-astra$")}

DAY = 24 * 3600
LIST_TIMEOUT = 5


def is_auto(model) -> bool:
    return not model or str(model).strip().lower() == AUTO


def _kind(pcfg: dict) -> str:
    t = pcfg.get("type") or ""
    return "local" if t == "openai_compatible" else t


def _cache_path():
    from .config import state_dir
    return state_dir() / "models.json"


def _cache_key(pcfg: dict) -> str:
    if _kind(pcfg) != "local":
        return pcfg.get("type") or ""
    return "local|%s" % (pcfg.get("base_url") or "").rstrip("/")


def _read_cache() -> dict:
    try:
        data = json.loads(_cache_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(key: str, model: str, at: float):
    data = _read_cache()
    data[key] = {"model": model, "at": at}
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, p)
    except OSError:
        pass


def list_models(pcfg: dict, key: str) -> list:
    """The provider's model list, as dicts with at least "id"."""
    from .providers.base import http_json
    kind = _kind(pcfg)
    if kind == "anthropic":
        out = http_json("https://api.anthropic.com/v1/models?limit=100", "GET",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"}, None,
                        timeout=LIST_TIMEOUT)
    else:
        base = {"xai": "https://api.x.ai/v1", "openai": "https://api.openai.com/v1"}.get(kind)
        base = (pcfg.get("base_url") or base or "").rstrip("/")
        headers = {"Authorization": "Bearer " + key} if key else {}
        out = http_json(base + "/models", "GET", headers, None, timeout=LIST_TIMEOUT)
    data = out.get("data") if isinstance(out, dict) else None
    return [m for m in data or [] if isinstance(m, dict) and m.get("id")]


def pick(kind: str, models: list) -> str:
    """The newest flagship in the list, or "" when there is none."""
    if kind == "local":
        return str(models[0]["id"]) if models else ""
    if kind == "anthropic":
        ours = [m for m in models if m.get("line") == "opus"
                and m.get("lifecycle", "active") == "active"]
        ours.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
        return str(ours[0]["id"]) if ours else ""
    pat = PATTERNS.get(kind)
    if not pat:
        return ""
    ours = [m for m in models if pat.match(str(m["id"]))]

    def created(m):
        try:
            return int(m.get("created") or 0)
        except (TypeError, ValueError):
            return 0
    ours.sort(key=created, reverse=True)
    return str(ours[0]["id"]) if ours else ""


def cached(pcfg: dict) -> str:
    """What "auto" means right now without asking the provider: the cached pick, else
    the fallback ("" for a local server that hasn't been asked yet)."""
    hit = _read_cache().get(_cache_key(pcfg)) or {}
    return hit.get("model") or FALLBACK.get(_kind(pcfg), "")


def resolve(pcfg: dict, key: str = "", refresh: bool = False):
    """(model, source) for a provider whose model is "auto". source is "list" when it
    came from the provider's model list, "cache" or "fallback" otherwise."""
    kind = _kind(pcfg)
    ck = _cache_key(pcfg)
    hit = _read_cache().get(ck) or {}
    now = time.time()
    if not refresh and hit.get("model") and now - float(hit.get("at") or 0) < DAY:
        return hit["model"], "cache"
    model = ""
    try:
        model = pick(kind, list_models(pcfg, key))
    except Exception:  # noqa: BLE001 - any failure falls back
        model = ""
    if model:
        _write_cache(ck, model, now)
        return model, "list"
    fallback = hit.get("model") or FALLBACK.get(kind, "")
    if fallback:
        # Try the list again in an hour, not on every request.
        _write_cache(ck, fallback, now - DAY + 3600)
    return fallback, "fallback"
