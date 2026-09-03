"""
Maps a TemplateVersion's adapter_ref string to the actual Python adapter
class to invoke. This IS the "shortcut" documented in the approved Phase 4
plan: templates point at existing, hand-written adapters rather than being
interpreted by a generic declarative engine. Should never raise in normal
operation — TemplateVersion.adapter_ref values are only ever set by
bootstrap.py, which only ever uses registered keys.

Phase 5: "vision_layout_adapter" needs a constructed VisionProvider, which
depends on config (mock vs. real, model names) — so get_adapter takes an
additive optional config param, used only for that one adapter_ref.
"""
from extraction.legacy_mathongo_adapter import LegacyMathonGoAdapter
from extraction.vision_layout_adapter import VisionLayoutAdapter
from providers.fallback_vision import FallbackVisionProvider
from providers.mock_vision import MockVisionProvider
from providers.openai_compatible_vision import OpenAICompatibleVisionProvider

ADAPTER_REGISTRY = {
    "legacy_mathongo_adapter": LegacyMathonGoAdapter,
    "vision_layout_adapter": None,  # constructed via _build_vision_provider below
}


def _build_openai_fallback(config):
    return OpenAICompatibleVisionProvider(
        base_url="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        model=config.vision_fallback_model,
        # GPT-5-series models default to a nonzero reasoning effort, which
        # OpenAI's own API rejects when combined with function tools on
        # /v1/chat/completions — confirmed live ("Function tools with
        # reasoning_effort are not supported... set reasoning_effort to
        # 'none'"). Not a guess.
        reasoning_effort="none",
        provider_label="openai",
    )


def build_vision_provider(config):
    """DeepSeek (primary) + OpenAI (fallback for hard API failures only —
    see providers.fallback_vision's docstring for why this is narrower than
    the real DeepSeek->OpenAI escalation mechanism, which lives in
    extraction.vision_escalation and is validation-driven, not error-driven).
    Defaults to MockVisionProvider — real API calls require an explicit
    config.vision_provider == "real" opt-in.

    If OPENAI_API_KEY isn't set (a deliberate DeepSeek-only run), the OpenAI
    client construction itself raises — that's treated as "no fallback
    available" and the bare DeepSeek provider is returned instead of the
    wrapper, rather than crashing every vision-document ingestion outright."""
    if config is not None and getattr(config, "vision_provider", "mock") == "real":
        primary = OpenAICompatibleVisionProvider(
            base_url="https://api.deepseek.com",
            api_key_env_var="DEEPSEEK_API_KEY",
            model=config.vision_primary_model,
            # deepseek-v4-flash-vision-exp defaults to "thinking mode",
            # which rejects a forced tool_choice — confirmed live against
            # the real API. Disabled so structured tool-output works.
            extra_body={"thinking": {"type": "disabled"}},
            provider_label="deepseek",
        )
        try:
            fallback = _build_openai_fallback(config)
        except Exception:
            return primary
        return FallbackVisionProvider(primary=primary, fallback=fallback)
    return MockVisionProvider()


def build_escalation_provider(config):
    """The provider extraction.vision_escalation actually escalates TO —
    OpenAI alone, not build_vision_provider()'s DeepSeek-primary wrapper.
    Using the wrapper here would be wrong: escalation triggers on DeepSeek
    SUCCEEDING with low-quality output, not on DeepSeek's call erroring, so
    a FallbackVisionProvider(primary=DeepSeek, ...) would just call DeepSeek
    again first and never actually reach OpenAI."""
    if config is not None and getattr(config, "vision_provider", "mock") == "real":
        return _build_openai_fallback(config)
    return MockVisionProvider()


def get_adapter(adapter_ref: str, config=None):
    if adapter_ref not in ADAPTER_REGISTRY:
        raise KeyError(f"no adapter registered for adapter_ref={adapter_ref!r}")
    if adapter_ref == "vision_layout_adapter":
        return VisionLayoutAdapter(build_vision_provider(config))
    return ADAPTER_REGISTRY[adapter_ref]()
