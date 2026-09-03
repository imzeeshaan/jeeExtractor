"""
Narrow, error-only fallback — NOT the primary DeepSeek->OpenAI cost/accuracy
mechanism (that's src/extraction/vision_escalation.py, which is
validation-driven and per-question). This wrapper only handles the case
where the primary provider's API call itself fails outright (rate limit,
connection error, bad response shape, or any other exception raised while
building the response).

Retries the primary a few times (with backoff) before falling back at all —
most transient failures (rate limits, timeouts) clear up on a retry, so
this avoids reaching for the fallback (confirmed, separately, to sometimes
return lower-quality regions with artificially high self-reported
confidence) for a one-off blip.
"""
import time

from models.vision import LayoutResponse, TranscriptionResult
from providers.vision_base import VisionProvider

_DEFAULT_MAX_RETRIES = 2
_DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
# Backoff values are a reasonable starting default, not verified against
# DeepSeek's actual rate-limit reset window — tune if real usage shows the
# fallback still triggering on what were actually recoverable blips.


class FallbackVisionProvider:
    def __init__(self, primary: VisionProvider, fallback: VisionProvider,
                 max_retries: int = _DEFAULT_MAX_RETRIES,
                 retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS):
        self._primary = primary
        self._fallback = fallback
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    def _call_primary_with_retries(self, method_name: str, *args):
        last_exc = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                time.sleep(self._retry_backoff_seconds * attempt)
            try:
                return getattr(self._primary, method_name)(*args), None
            except Exception as exc:
                # Catches any exception, not just RuntimeError -- a
                # non-RuntimeError here used to propagate straight past this
                # wrapper uncaught, silently skipping the fallback entirely.
                last_exc = exc
        return None, last_exc

    def detect_layout(self, page_image_path: str, page_number: int, context: dict) -> LayoutResponse:
        result, exc = self._call_primary_with_retries("detect_layout", page_image_path, page_number, context)
        if result is not None:
            return result
        fallback_result = self._fallback.detect_layout(page_image_path, page_number, context)
        fallback_result.warnings = [
            f"primary provider failed after {self._max_retries + 1} attempt(s) ({exc}); used fallback",
        ] + fallback_result.warnings
        return fallback_result

    def transcribe_region(self, crop_image_path: str, context: dict) -> TranscriptionResult:
        result, exc = self._call_primary_with_retries("transcribe_region", crop_image_path, context)
        if result is not None:
            return result
        fallback_result = self._fallback.transcribe_region(crop_image_path, context)
        fallback_result.warnings = [
            f"primary provider failed after {self._max_retries + 1} attempt(s) ({exc}); used fallback",
        ] + fallback_result.warnings
        return fallback_result
