from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    UPLOAD_DIR: str = "/app/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    AUTH_ENABLED: bool = True
    # Comma-separated list of allowed CORS origins, e.g. http://1.2.3.4:8080
    ALLOWED_ORIGINS: str = ""
    LOG_LEVEL: str = "INFO"

    # JWT auth
    JWT_SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Ollama (local LLM)
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "gemma4:e4b"
    OLLAMA_ENRICHMENT_CONCURRENCY: int = 1
    OLLAMA_TIMEOUT_SECONDS: int = 120

    # Gemini (Google cloud LLM)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_ENRICHMENT_CONCURRENCY: int = 5
    GEMINI_TIMEOUT_SECONDS: int = 30

    # Default provider used when the enrich buttons don't specify one
    # Accepted values: "ollama" | "gemini"
    ENRICHMENT_PROVIDER: str = "ollama"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def check_jwt_key_when_auth_enabled(self) -> "Settings":
        if self.AUTH_ENABLED and not self.JWT_SECRET_KEY:
            raise ValueError(
                "JWT_SECRET_KEY must be set when AUTH_ENABLED=true. "
                "Generate one with: openssl rand -hex 32"
            )
        return self


settings = Settings()
