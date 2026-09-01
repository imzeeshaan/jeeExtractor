"""
Maps a TemplateVersion's adapter_ref string to the actual Python adapter
class to invoke. This IS the "shortcut" documented in the approved Phase 4
plan: templates point at existing, hand-written adapters rather than being
interpreted by a generic declarative engine. Should never raise in normal
operation — TemplateVersion.adapter_ref values are only ever set by
bootstrap.py, which only ever uses registered keys.
"""
from extraction.legacy_mathongo_adapter import LegacyMathonGoAdapter

ADAPTER_REGISTRY = {
    "legacy_mathongo_adapter": LegacyMathonGoAdapter,
}


def get_adapter(adapter_ref: str):
    if adapter_ref not in ADAPTER_REGISTRY:
        raise KeyError(f"no adapter registered for adapter_ref={adapter_ref!r}")
    return ADAPTER_REGISTRY[adapter_ref]()
