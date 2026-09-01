"""
Single source of app configuration. Nothing else in src/ reads os.environ
directly — everything goes through get_config().
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from functools import lru_cache

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

    config = AppConfig(
        data_dir=data_dir,
        db_path=db_path,
        uploads_dir=data_dir / "uploads",
        rendered_pages_dir=data_dir / "rendered_pages",
        crops_dir=data_dir / "crops",
        templates_dir=data_dir / "templates",
        render_dpi=render_dpi,
        db_echo=db_echo,
    )
    config.ensure_directories()
    return config
