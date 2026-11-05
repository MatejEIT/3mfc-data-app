"""Runtime configuration from environment variables / Data App secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    kbc_url: str
    kbc_token: str
    app_bucket: str            # writeback bucket, e.g. in.c-3mfc-app
    staging_db: str            # Snowflake database holding the project's storage
    bucket_weekly: str
    bucket_bootstrap: str
    bucket_fx: str
    backend: str               # "keboola" | "memory"
    dev_user: str              # local development only
    dev_role: str
    app_url: str

    @property
    def is_dev(self) -> bool:
        return bool(self.dev_user)


def load_settings() -> Settings:
    return Settings(
        kbc_url=_env("KBC_URL", "https://connection.europe-west3.gcp.keboola.com"),
        kbc_token=_env("KBC_TOKEN"),
        app_bucket=_env("APP_BUCKET", "in.c-3mfc-app"),
        staging_db=_env("STAGING_DB", "KBC_EUW3_810"),
        bucket_weekly=_env("BUCKET_WEEKLY", "out.c-3MFC-Weekly-Cash-Flow-Facts--App-Prefill-staging"),
        bucket_bootstrap=_env("BUCKET_BOOTSTRAP", "out.c-3MFC-App-Backend-Bootstrap-no-writes"),
        bucket_fx=_env("BUCKET_FX", "out.c-3MFC-VIT-Entity-Dictionary--Prior-Month-FX-staging"),
        backend=_env("APP_BACKEND", "keboola"),
        dev_user=_env("APP_DEV_USER"),
        dev_role=_env("APP_DEV_ROLE", "HQ"),
        app_url=_env("APP_PUBLIC_URL"),
    )
