import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("HUB_DATA_DIR", "./data")).resolve())
    database_url: str = field(default_factory=lambda: os.environ.get("HUB_DATABASE_URL", ""))
    # Base URLs of GPU workers on the DGX Spark. Empty = adapter reports itself unavailable.
    trellis2_url: str = field(default_factory=lambda: os.environ.get("HUB_TRELLIS2_URL", ""))
    trellis_text_url: str = field(default_factory=lambda: os.environ.get("HUB_TRELLIS_TEXT_URL", ""))
    hunyuan_url: str = field(default_factory=lambda: os.environ.get("HUB_HUNYUAN_URL", ""))
    # An existing Hunyuan3D deployment running Tencent's upstream api_server.py (default port 8081).
    hunyuan_api_url: str = field(default_factory=lambda: os.environ.get("HUB_HUNYUAN_API_URL", ""))
    hunyuan_api_model: str = field(default_factory=lambda: os.environ.get("HUB_HUNYUAN_API_MODEL", ""))
    img2threejs_url: str = field(default_factory=lambda: os.environ.get("HUB_IMG2THREEJS_URL", "http://img2threejs.ailab.lan:8099"))
    lease_seconds: int = int(os.environ.get("HUB_LEASE_SECONDS", "900"))
    # Shared-secret token for the API. Empty disables auth (dev only).
    api_token: str = field(default_factory=lambda: os.environ.get("HUB_API_TOKEN", ""))

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'hub.db'}"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"


def get_settings() -> Settings:
    return Settings()
