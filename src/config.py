"""
Single source of app configuration. Nothing else in src/ reads os.environ
directly — everything goes through get_config().
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AppConfig:
    data_dir: Path
    db_path: Path
    uploads_dir: Path
    rendered_pages_dir: Path
    crops_dir: Path
    templates_dir: Path
    render_dpi: int = 300
    db_echo: bool = False
    # Phase 5 (vision layout fallback). "mock" is the load-bearing default —
    # it guarantees review_app.py/the test suite never spend real API money
    # unless a developer deliberately opts in via JEE_VISION_PROVIDER=real.
    # No API-key fields here on purpose: OpenAICompatibleVisionProvider reads
    # DEEPSEEK_API_KEY/OPENAI_API_KEY from the environment directly, so a key
    # is never stored, printed, or accidentally included in a log line via
    # this dataclass's repr.
    vision_provider: Literal["mock", "real"] = "mock"
    vision_primary_model: str = "deepseek-flash"
    vision_fallback_model: str = "gpt-5.6-luna"
    # Concurrent per-page/per-question API dispatch (Phase 5 follow-up). A
    # conservative default, NOT verified against DeepSeek/OpenAI's actual
    # published concurrent-request limits -- tunable via env var if real
    # usage ever hits a 429, without a code change.
    vision_concurrency: int = 5

    def ensure_directories(self):
        for d in (self.data_dir, self.uploads_dir, self.rendered_pages_dir,
                  self.crops_dir, self.templates_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    load_dotenv(_REPO_ROOT / ".env", override=False)

    data_dir = Path(os.environ.get("JEE_DATA_DIR", str(_REPO_ROOT / "data"))).resolve()
    db_path = Path(os.environ.get("JEE_DB_PATH", str(data_dir / "app.db")))
    render_dpi = int(os.environ.get("JEE_RENDER_DPI", "300"))
    db_echo = os.environ.get("JEE_DB_ECHO", "false").lower() in ("1", "true", "yes")
    vision_provider = os.environ.get("JEE_VISION_PROVIDER", "mock")
    vision_primary_model = os.environ.get("JEE_VISION_PRIMARY_MODEL", "deepseek-flash")
    vision_fallback_model = os.environ.get("JEE_VISION_FALLBACK_MODEL", "gpt-5.6-luna")
    vision_concurrency = int(os.environ.get("JEE_VISION_CONCURRENCY", "5"))

    config = AppConfig(
        data_dir=data_dir,
        db_path=db_path,
        uploads_dir=data_dir / "uploads",
        rendered_pages_dir=data_dir / "rendered_pages",
        crops_dir=data_dir / "crops",
        templates_dir=data_dir / "templates",
        render_dpi=render_dpi,
        db_echo=db_echo,
        vision_provider=vision_provider,
        vision_primary_model=vision_primary_model,
        vision_fallback_model=vision_fallback_model,
        vision_concurrency=vision_concurrency,
    )
    config.ensure_directories()
    return config
