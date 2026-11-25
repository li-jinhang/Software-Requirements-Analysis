from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "data"
    mock_llm: bool = False  # Legacy flag disables environment-provided models.
    model_timeout_seconds: int = Field(default=180, ge=5, le=900)
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    embedding_provider: Literal["local_lite", "api"] = "local_lite"
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    seed_demo: bool = True
    demo_build_id: str = ""  # Set to a release ID when deploying separate frontend/backend artifacts.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]


settings = Settings()
settings.data_dir = settings.data_dir.resolve()
settings.data_dir.mkdir(parents=True, exist_ok=True)
