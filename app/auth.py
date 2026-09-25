"""Connexion à Salesforce avec le compte du commercial (OAuth 2.0 « web server » + PKCE).

Le commercial se connecte avec ses identifiants Salesforce habituels (ou le SSO Volvo si l'org
l'utilise). L'application ne voit jamais son mot de passe ; elle reçoit un jeton limité aux
droits de cet utilisateur.

Les jetons sont gardés côté serveur (le cookie ne contient qu'un identifiant de session signé).
Stockage en mémoire : suffisant pour un pilote sur une seule instance ; en production, utiliser
un stockage partagé et chiffré (ex. Redis) — voir docs/proposition.md.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

from .config import Settings

SESSION_TTL = 12 * 3600
STATE_TTL = 600


class TokenStore:
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def put(self, data: dict) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[sid] = {**data, "created": time.time()}
        return sid

    def get(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        with self._lock:
            data = self._sessions.get(sid)
            if data and time.time() - data["created"] > SESSION_TTL:
                self._sessions.pop(sid, None)
                return None
            return data

    def update_token(self, sid: str, token: str) -> None:
        with self._lock:
            if sid in self._sessions:
                self._sessions[sid]["access_token"] = token

    def drop(self, sid: str | None) -> None:
        with self._lock:
            self._sessions.pop(sid or "", None)

    def new_state(self) -> tuple[str, str]:
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        with self._lock:
            now = time.time()
            self._pending = {k: v for k, v in self._pending.items() if now - v[1] < STATE_TTL}
            self._pending[state] = (verifier, now)
        return state, verifier

    def pop_state(self, state: str) -> str | None:
        with self._lock:
            item = self._pending.pop(state, None)
        if not item or time.time() - item[1] > STATE_TTL:
            return None
        return item[0]


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(settings: Settings, state: str, verifier: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.sf_client_id,
        "redirect_uri": settings.sf_redirect_uri,
        "state": state,
        "code_challenge": challenge(verifier),
        "code_challenge_method": "S256",
        "scope": "api refresh_token id",
    }
    return f"{settings.sf_login_url}/services/oauth2/authorize?{urlencode(params)}"


def exchange_code(settings: Settings, code: str, verifier: str, transport: httpx.BaseTransport | None = None) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.sf_client_id,
        "redirect_uri": settings.sf_redirect_uri,
        "code_verifier": verifier,
    }
    if settings.sf_client_secret:
        data["client_secret"] = settings.sf_client_secret
    with httpx.Client(timeout=20.0, transport=transport) as http:
        resp = http.post(f"{settings.sf_login_url}/services/oauth2/token", data=data)
        resp.raise_for_status()
        token = resp.json()
        ident = http.get(token["id"], headers={"Authorization": f"Bearer {token['access_token']}"})
        ident.raise_for_status()
        who = ident.json()
    return {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "instance_url": token["instance_url"],
        "user_id": who.get("user_id"),
        "display_name": who.get("display_name") or who.get("username"),
    }


def refresh_access_token(settings: Settings, refresh_token: str | None,
                         transport: httpx.BaseTransport | None = None) -> str | None:
    if not refresh_token:
        return None
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": settings.sf_client_id}
    if settings.sf_client_secret:
        data["client_secret"] = settings.sf_client_secret
    try:
        with httpx.Client(timeout=20.0, transport=transport) as http:
            resp = http.post(f"{settings.sf_login_url}/services/oauth2/token", data=data)
            resp.raise_for_status()
            return resp.json()["access_token"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
