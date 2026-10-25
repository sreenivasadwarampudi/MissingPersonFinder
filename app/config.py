from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # general
    app_name: str = "Reunite"
    env: str = "dev"  # dev | prod
    secret_key: str = DEFAULT_SECRET
    database_url: str = "sqlite:///./data/app.db"
    frontend_origins: str = "http://localhost:5173"
    trust_proxy: bool = False

    # auth
    access_token_minutes: int = 60 * 12
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    moderator_emails: str = ""

    # email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""

    # telegram
    telegram_bot_token: str = ""
    telegram_moderator_chat_id: str = ""

    # storage
    storage_backend: str = "local"  # local | supabase
    local_storage_dir: str = "./data/uploads"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "reunite-private"

    # uploads
    max_upload_mb: int = 10
    max_image_side: int = 2000

    # faces
    face_backend: str = "insightface"  # insightface | deepface | mock
    insightface_model: str = "buffalo_l"
    deepface_model: str = "Facenet512"
    match_threshold: float | None = None
    match_top_k: int = 5

    # restoration
    restore_backend: str = "basic"  # basic | none | gfpgan | hf_space
    restore_min_identity: float = 0.5  # drop a cleaned image if its face drifts from the original
    gfpgan_model_path: str = ""
    hf_space_id: str = "sczhou/CodeFormer"
    hf_space_api_name: str = "/inference"

    # geo
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_contact: str = ""

    # user-facing text
    ngo_name: str = ""
    ngo_contact: str = ""
    grievance_email: str = ""

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def moderators(self) -> set[str]:
        return {e.strip().lower() for e in self.moderator_emails.split(",") if e.strip()}

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"

    @property
    def effective_threshold(self) -> float:
        if self.match_threshold is not None:
            return self.match_threshold
        return {"insightface": 0.35, "deepface": 0.60, "mock": 0.90}.get(self.face_backend, 0.5)


@lru_cache
def get_settings() -> Settings:
    return Settings()
