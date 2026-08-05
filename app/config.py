import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    dashboard_password: str = os.getenv("DASHBOARD_PASSWORD", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash-0731")
    facebook_page_id: str = os.getenv("FACEBOOK_PAGE_ID", "")
    facebook_token: str = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    facebook_version: str = os.getenv("FACEBOOK_GRAPH_VERSION", "vXX.X")

    @property
    def facebook_ready(self) -> bool:
        return bool(self.facebook_page_id and self.facebook_token and "XX" not in self.facebook_version)


settings = Settings()
