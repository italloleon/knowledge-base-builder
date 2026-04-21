from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    UPLOAD_DIR: str = "/app/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    AUTH_ENABLED: bool = False
    API_KEY: str = ""
    LOG_LEVEL: str = "INFO"

    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "gemma4:e4b"
    OLLAMA_ENRICHMENT_ENABLED: bool = True
    OLLAMA_ENRICHMENT_CONCURRENCY: int = 1
    OLLAMA_TIMEOUT_SECONDS: int = 120

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
