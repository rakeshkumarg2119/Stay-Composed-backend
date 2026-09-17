from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    db_name: str = "stay_composed"

    # SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "Stay Composed - Campus Blood Alert"

    # CORS
    allowed_origins: str = "http://localhost:3000"

    # CLIP
    clip_model_name: str = "clip-ViT-B-32"

    # Matching / verification
    match_min_confidence: int = 40
    claim_max_attempts: int = 3
    claim_cooldown_minutes: int = 30
    claim_timeout_hours: int = 48

    # Chat — unlocks on AI match (50%+ allows text/category/location match even without claimant photo)
    chat_min_confidence: int = 40
    chat_max_messages_per_minute: int = 20
    chat_message_max_length: int = 1000

    # Push notifications (FCM) — path to the service-account private-key
    # JSON from Firebase Console > Project Settings > Service Accounts.
    # NOT the Flutter app's google-services.json (that one's client-only).
    # Empty string = push is a no-op everywhere (see push_service.py).
    firebase_credentials_path: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()