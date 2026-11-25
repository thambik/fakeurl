from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./smartattend.db"
    jwt_secret_key: str = "development-only-change-this-secret"
    jwt_expire_minutes: int = 60
    biometric_encryption_key: str = ""
    bootstrap_token: str = ""
    insightface_model: str = "buffalo_l"
    insightface_root: str = "./models"
    face_match_threshold: float = 0.45
    auto_mark_recognized: bool = False
    liveness_provider: str = ""
    cors_origins: str = "http://localhost:5173"
    school_name: str = "SmartAttend School"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

