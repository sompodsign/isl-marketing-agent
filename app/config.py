import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("APP_ENV", "development")
    run_scheduler: bool = os.getenv("ENABLE_INTERNAL_SCHEDULER", "true").lower() == "true"
    dashboard_password: str = os.getenv("DASHBOARD_PASSWORD", "")
    writer_provider: str = os.getenv("WRITER_PROVIDER", "auto").lower()
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash-0731")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    openai_reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "low")
    facebook_page_id: str = os.getenv("FACEBOOK_PAGE_ID", "")
    facebook_token: str = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    facebook_version: str = os.getenv("FACEBOOK_GRAPH_VERSION", "vXX.X")
    linkedin_token: str = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    linkedin_author_urn: str = os.getenv("LINKEDIN_AUTHOR_URN", "")
    linkedin_api_version: str = os.getenv("LINKEDIN_API_VERSION", "202506")

    @property
    def facebook_ready(self) -> bool:
        return bool(self.facebook_page_id and self.facebook_token and "XX" not in self.facebook_version)

    @property
    def linkedin_ready(self) -> bool:
        return bool(self.linkedin_token and self.linkedin_author_urn)

    @property
    def linkedin_author(self) -> str:
        """Accept either a full URN or a bare numeric Company Page ID."""
        value = self.linkedin_author_urn.strip()
        if value.isdigit():
            return f"urn:li:organization:{value}"
        return value

    @property
    def production(self) -> bool:
        return self.environment.lower() == "production"


settings = Settings()
