import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    places_api_key: SecretStr
    gmail_address: str = ""
    gmail_app_password: SecretStr = SecretStr("")
    langsmith_tracing: str = "false"
    langsmith_endpoint: str = ""
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    def model_post_init(self, __context):
        os.environ.setdefault("LANGSMITH_TRACING", self.langsmith_tracing)
        os.environ.setdefault("LANGSMITH_ENDPOINT", self.langsmith_endpoint)
        os.environ.setdefault("LANGSMITH_API_KEY", self.langsmith_api_key.get_secret_value())
        os.environ.setdefault("LANGSMITH_PROJECT", self.langsmith_project)


settings = Settings()