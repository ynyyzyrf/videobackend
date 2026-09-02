from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8010
    database_url: str = "mysql+pymysql://weekly_report:change_me@127.0.0.1:3306/weekly_report?charset=utf8mb4"
    backend_api_key: str = ""
    public_base_url: str = ""
    asset_storage_dir: str = "/data/assets"

    dify_api_base: str = "https://ai-dashboard.solarifyai.com/v1"
    dify_api_key: str = ""
    dify_user_prefix: str = "daostore"

    video_composer_base_url: str = "https://testsmarties.yamimeal.ca"
    video_composer_api_key: str = ""
    video_task_poll_interval_seconds: int = 3
    video_task_timeout_seconds: int = 600
    video_task_max_retries: int = 2

    ffmpeg_api_url: str = "https://verygoodffmpeg.com/api/ffmpeg?wait=true"
    ffmpeg_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
