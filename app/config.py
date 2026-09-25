"""Paramètres de l'application, lus depuis les variables d'environnement.

Deux modes :
- ``mock`` : démonstration, Salesforce est simulé en mémoire (aucune connexion réelle) ;
- ``live`` : connexion à une org Salesforce via OAuth 2.0 (flux « web server » + PKCE).
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


@dataclass
class Settings:
    app_mode: str = "mock"
    data_dir: Path = BASE_DIR / "data"
    mapping_path: Path = BASE_DIR / "config" / "field_mapping.json"
    session_secret: str = ""
    cookie_secure: bool = False
    draft_retention_days: int = 14
    max_upload_mb: int = 8
    max_pdf_pages: int = 40
    demo_samples: bool = True

    sf_login_url: str = "https://login.salesforce.com"
    sf_client_id: str = ""
    sf_client_secret: str = ""
    sf_redirect_uri: str = "http://localhost:8000/auth/callback"
    sf_api_version: str = "v62.0"

    mapping: dict = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.app_mode == "live"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    mode = os.getenv("APP_MODE", "mock").strip().lower()
    if mode not in {"mock", "live"}:
        raise RuntimeError("APP_MODE doit valoir 'mock' ou 'live'.")

    settings = Settings(
        app_mode=mode,
        data_dir=Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))),
        mapping_path=Path(os.getenv("FIELD_MAPPING_PATH", str(BASE_DIR / "config" / "field_mapping.json"))),
        session_secret=os.getenv("SESSION_SECRET", ""),
        cookie_secure=_bool("COOKIE_SECURE", mode == "live"),
        draft_retention_days=int(os.getenv("DRAFT_RETENTION_DAYS", "14")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "8")),
        max_pdf_pages=int(os.getenv("MAX_PDF_PAGES", "40")),
        demo_samples=_bool("DEMO_SAMPLES", mode == "mock"),
        sf_login_url=os.getenv("SF_LOGIN_URL", "https://login.salesforce.com").rstrip("/"),
        sf_client_id=os.getenv("SF_CLIENT_ID", ""),
        sf_client_secret=os.getenv("SF_CLIENT_SECRET", ""),
        sf_redirect_uri=os.getenv("SF_REDIRECT_URI", "http://localhost:8000/auth/callback"),
        sf_api_version=os.getenv("SF_API_VERSION", "v62.0"),
    )

    if not settings.session_secret:
        if settings.is_live:
            raise RuntimeError("SESSION_SECRET est obligatoire en mode live.")
        # En démonstration, un secret éphémère suffit (les sessions expirent au redémarrage).
        settings.session_secret = secrets.token_urlsafe(32)

    if settings.is_live and not settings.sf_client_id:
        raise RuntimeError("SF_CLIENT_ID est obligatoire en mode live.")

    with settings.mapping_path.open(encoding="utf-8") as fh:
        settings.mapping = json.load(fh)
    settings.mapping.setdefault("api_version", settings.sf_api_version)
    if os.getenv("SF_API_VERSION"):
        settings.mapping["api_version"] = settings.sf_api_version

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
