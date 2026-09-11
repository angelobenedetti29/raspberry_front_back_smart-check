import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Load backend/.env as early as possible so every consumer sees the values.
load_env_file(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    central_lotes_base_url: str
    central_lotes_api_key: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        central_lotes_base_url=(
            os.getenv("CENTRAL_LOTE_BASE_URL")
            or os.getenv("CENTRAL_LOTES_BASE_URL")
            or ""
        ),
        central_lotes_api_key=(
            os.getenv("CENTRAL_LOTE_API_KEY")
            or os.getenv("CENTRAL_LOTES_API_KEY")
            or ""
        ),
    )
