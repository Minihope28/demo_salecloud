import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.main import create_app

MAPPING = json.loads((BASE_DIR / "config" / "field_mapping.json").read_text(encoding="utf-8"))
HEADERS = {"X-Requested-With": "dossier-client"}


def make_settings(tmp_path: Path, mode: str = "mock") -> Settings:
    return Settings(
        app_mode=mode,
        data_dir=tmp_path,
        session_secret="test-secret",
        cookie_secure=False,
        demo_samples=True,
        ocr_mode="off",
        postal_codes_path=tmp_path / "absent" / "codes_postaux.csv",
        sf_login_url="https://login.example.test",
        sf_client_id="client-id",
        sf_redirect_uri="http://testserver/auth/callback",
        mapping=copy.deepcopy(MAPPING),
    )


@pytest.fixture
def mapping():
    return copy.deepcopy(MAPPING)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(make_settings(tmp_path)))
