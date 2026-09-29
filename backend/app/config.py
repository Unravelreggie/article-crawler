from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./literature.sqlite3"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "change-me"
    s3_secret_key: str = "change-me"
    s3_bucket: str = "literature"
    contact_email: str = ""
    ncbi_api_key: str = ""
    openalex_api_key: str = ""
    max_upload_mb: int = 25
    allow_fake_ip_dns: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def settings() -> Settings:
    return Settings()
