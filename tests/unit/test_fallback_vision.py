"""
Phase 5: FallbackVisionProvider's narrow error-only behavior — NOT the
primary DeepSeek->OpenAI escalation mechanism (that's
test_vision_escalation.py). This only covers "the primary provider's API
call itself errored," including the retry-before-fallback behavior added
after a live-observed case where a transient DeepSeek failure fell over to
a lower-quality OpenAI response.

retry_backoff_seconds=0 everywhere here so these tests stay fast — the
actual backoff duration is a runtime tuning knob, not something worth
covering with a real sleep in a unit test.
"""
from models.vision import LayoutResponse, TranscriptionResult
from providers.fallback_vision import FallbackVisionProvider


class _RaisingProvider:
    def detect_layout(self, page_image_path, page_number, context):
        raise RuntimeError("simulated API failure")

    def transcribe_region(self, crop_image_path, context):
        raise RuntimeError("simulated API failure")


class _CannedProvider:
    def detect_layout(self, page_image_path, page_number, context):
        return LayoutResponse(page_number=page_number, regions=[], reading_order=[], warnings=["canned"])

    def transcribe_region(self, crop_image_path, context):
        return TranscriptionResult(text="canned text", unreadable=False, warnings=["canned"])


class _FailsNTimesThenSucceeds:
    """Simulates a transient failure that clears up after a couple of
    retries -- the real-world case retries exist for."""
    def __init__(self, fail_count):
        self._fail_count = fail_count
        self.call_count = 0

    def detect_layout(self, page_image_path, page_number, context):
        self.call_count += 1
        if self.call_count <= self._fail_count:
            raise RuntimeError(f"simulated transient failure #{self.call_count}")
        return LayoutResponse(page_number=page_number, regions=[], reading_order=[], warnings=["real"])

    def transcribe_region(self, crop_image_path, context):
        raise NotImplementedError


def test_falls_back_to_secondary_after_retries_exhausted():
    provider = FallbackVisionProvider(primary=_RaisingProvider(), fallback=_CannedProvider(),
                                       max_retries=2, retry_backoff_seconds=0)
    result = provider.detect_layout("page.png", 1, {})
    assert result.warnings[0].startswith("primary provider failed after 3 attempt(s)")
    assert "canned" in result.warnings

    transcribed = provider.transcribe_region("crop.png", {})
    assert transcribed.text == "canned text"
    assert transcribed.warnings[0].startswith("primary provider failed after 3 attempt(s)")


def test_never_calls_fallback_when_primary_succeeds():
    calls = {"fallback_called": False}

    class _TrackingFallback(_CannedProvider):
        def detect_layout(self, page_image_path, page_number, context):
            calls["fallback_called"] = True
            return super().detect_layout(page_image_path, page_number, context)

    provider = FallbackVisionProvider(primary=_CannedProvider(), fallback=_TrackingFallback(),
                                       retry_backoff_seconds=0)
    result = provider.detect_layout("page.png", 1, {})
    assert calls["fallback_called"] is False
    assert result.warnings == ["canned"]


def test_recovers_via_retry_without_ever_calling_fallback():
    calls = {"fallback_called": False}

    class _TrackingFallback(_CannedProvider):
        def detect_layout(self, page_image_path, page_number, context):
            calls["fallback_called"] = True
            return super().detect_layout(page_image_path, page_number, context)

    primary = _FailsNTimesThenSucceeds(fail_count=2)
    provider = FallbackVisionProvider(primary=primary, fallback=_TrackingFallback(),
                                       max_retries=2, retry_backoff_seconds=0)
    result = provider.detect_layout("page.png", 1, {})

    assert primary.call_count == 3  # 1 initial + 2 retries
    assert calls["fallback_called"] is False
    assert result.warnings == ["real"]  # the real (eventually-successful) primary response, not the fallback's


def test_falls_back_when_retries_still_not_enough():
    primary = _FailsNTimesThenSucceeds(fail_count=5)  # fails more times than max_retries allows
    provider = FallbackVisionProvider(primary=primary, fallback=_CannedProvider(),
                                       max_retries=2, retry_backoff_seconds=0)
    result = provider.detect_layout("page.png", 1, {})

    assert primary.call_count == 3  # 1 initial + 2 retries, then gives up
    assert "canned" in result.warnings
