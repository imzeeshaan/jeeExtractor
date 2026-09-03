"""
Regression guard for a real bug hit live this session: OpenAI's strict
structured-output mode rejects a schema where a property exists in
"properties" but is missing from "required" — even nullable ones. Confirmed
directly against the real API (error: "'required' is... missing
question_number_hint"). This pins that every property in both tool schemas
stays listed in "required", so a future edit (e.g. adding a new field)
can't silently reintroduce the same live failure.
"""
from providers.openai_compatible_vision import _LAYOUT_TOOL_SCHEMA, _TRANSCRIBE_TOOL_SCHEMA


def _region_item_schema():
    return _LAYOUT_TOOL_SCHEMA["function"]["parameters"]["properties"]["regions"]["items"]


def test_layout_top_level_properties_all_required():
    params = _LAYOUT_TOOL_SCHEMA["function"]["parameters"]
    assert set(params["properties"].keys()) == set(params["required"])


def test_layout_region_item_properties_all_required():
    region_schema = _region_item_schema()
    assert set(region_schema["properties"].keys()) == set(region_schema["required"])


def test_transcribe_properties_all_required():
    params = _TRANSCRIBE_TOOL_SCHEMA["function"]["parameters"]
    assert set(params["properties"].keys()) == set(params["required"])
    assert "latex" in params["properties"]
    assert "latex" in params["required"]
