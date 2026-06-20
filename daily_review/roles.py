"""LLM 角色化模型配置 — 按任务角色选模型/提供商，统一入口。

用法:
    from roles import get_client, get_model
    client, model = get_client("deep")
    resp = client.messages.create(model=model, ...)

切换 Anthropic Fable 5: 改 PHASE = "fable5"（一行）
"""
import json
import os
from pathlib import Path

from anthropic import Anthropic

# ============================================================
# 阶段切换 — 2周后改为 "fable5"
# ============================================================
PHASE = "now"  # "now" | "fable5"

# ============================================================
# 模型配置
# ============================================================
CONFIG = {
    "now": {
        "deep":      ("deepseek", "claude-sonnet-4-6-20250514"),
        "synthesis": ("deepseek", "claude-sonnet-4-6-20250514"),
        "scan":      ("deepseek", "claude-haiku-4-5-20251001"),
    },
    "fable5": {
        "deep":      ("anthropic", "claude-fable-5"),
        "synthesis": ("deepseek",  "claude-sonnet-4-6-20250514"),
        "scan":      ("deepseek",  "claude-haiku-4-5-20251001"),
    },
}

PROVIDER_URLS = {
    "deepseek":  "https://api.deepseek.com/anthropic",
    "anthropic": None,
}


def _load_key(provider: str) -> str:
    key = ""
    if provider == "deepseek":
        key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    elif provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            env = data.get("env", {})
            if provider == "deepseek":
                key = env.get("ANTHROPIC_AUTH_TOKEN", "")
            elif provider == "anthropic":
                key = env.get("ANTHROPIC_API_KEY", "") or env.get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def _get_provider_and_model(role: str) -> tuple[str, str]:
    cfg = CONFIG[PHASE]
    if role not in cfg:
        raise ValueError(f"未知角色: {role}，可选: {list(cfg.keys())}")
    return cfg[role]


def get_client(role: str, timeout: int = 120) -> Anthropic:
    provider, _ = _get_provider_and_model(role)
    base_url = PROVIDER_URLS[provider]
    api_key = _load_key(provider)
    if base_url:
        return Anthropic(api_key=api_key, base_url=base_url, timeout=timeout)
    return Anthropic(api_key=api_key, timeout=timeout)


def get_model(role: str) -> str:
    _, model = _get_provider_and_model(role)
    return model


def cached_create(
    client: Anthropic,
    model: str,
    system: str,
    user_content: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> str:
    """带缓存的 LLM 调用 — 内容哈希去重，省重复推理成本。"""
    import llm_cache
    cached = llm_cache.get(model, system, user_content)
    if cached is not None:
        return cached
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    result = resp.content[0].text
    llm_cache.store(model, system, user_content, result)
    return result
