"""Stockage des dossiers en cours (brouillons) pour pouvoir interrompre et reprendre.

Un dossier = un répertoire ``data/drafts/<id>/`` contenant ``draft.json`` et les PDF déposés.
Les PDF sont supprimés dès qu'ils ont été envoyés dans Salesforce (minimisation des données).
Hébergement multi-instances : remplacer ce stockage fichier par une base partagée.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

EDITABLE_SECTIONS = {
    "company", "account_choice", "contact", "contact_choice", "segment", "opportunity", "verified",
}
LOCKED_STATUSES = {"submitting", "records_created", "completed"}
DOC_KINDS = {"rc", "fiscal"}


class DraftNotFound(KeyError):
    pass


class DraftLocked(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_draft(owner: str) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "owner": owner,
        "created_at": _now(),
        "updated_at": _now(),
        "status": "draft",
        "company": {},
        "company_sources": {},
        "hints": {},
        "account_choice": {"mode": "new"},
        "contact": {},
        "contact_choice": {"mode": "new"},
        "segment": {},
        "opportunity": {},
        "verified": False,
        "documents": {},
        "result": {},
    }


class DraftStore:
    def __init__(self, root: Path):
        self.root = root / "drafts"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ utilitaires
    def lock(self, draft_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(draft_id, threading.Lock())

    def _dir(self, draft_id: str) -> Path:
        if not draft_id.isalnum():
            raise DraftNotFound(draft_id)
        return self.root / draft_id

    def _path(self, draft_id: str) -> Path:
        return self._dir(draft_id) / "draft.json"

    # ------------------------------------------------------------------ CRUD
    def create(self, owner: str) -> dict:
        draft = new_draft(owner)
        self._dir(draft["id"]).mkdir(parents=True)
        self.save(draft)
        return draft

    def save(self, draft: dict) -> dict:
        draft["updated_at"] = _now()
        path = self._path(draft["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return draft

    def get(self, draft_id: str, owner: str) -> dict:
        path = self._path(draft_id)
        if not path.exists():
            raise DraftNotFound(draft_id)
        draft = json.loads(path.read_text(encoding="utf-8"))
        if draft.get("owner") != owner:
            raise DraftNotFound(draft_id)  # on ne révèle pas l'existence du dossier d'un autre utilisateur
        return draft

    def list(self, owner: str) -> list[dict]:
        items = []
        for path in self.root.glob("*/draft.json"):
            try:
                draft = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if draft.get("owner") == owner:
                items.append({
                    "id": draft["id"],
                    "company_name": draft.get("company", {}).get("company_name") or "(sans nom)",
                    "status": draft.get("status"),
                    "updated_at": draft.get("updated_at"),
                })
        return sorted(items, key=lambda d: d["updated_at"] or "", reverse=True)

    def update_sections(self, draft: dict, changes: dict) -> dict:
        if draft["status"] in LOCKED_STATUSES:
            raise DraftLocked("Ce dossier a déjà été envoyé dans Salesforce et ne peut plus être modifié.")
        for key, value in changes.items():
            if key not in EDITABLE_SECTIONS:
                continue
            if key == "verified":
                draft[key] = bool(value)
            elif isinstance(value, dict):
                draft[key] = {k: v for k, v in value.items() if isinstance(v, (str, int, float, bool)) or v is None}
        return self.save(draft)

    def delete(self, draft_id: str) -> None:
        shutil.rmtree(self._dir(draft_id), ignore_errors=True)

    # ------------------------------------------------------------------ fichiers
    def file_path(self, draft_id: str, kind: str) -> Path:
        if kind not in DOC_KINDS:
            raise ValueError(kind)
        return self._dir(draft_id) / f"{kind}.pdf"

    def store_file(self, draft_id: str, kind: str, data: bytes) -> None:
        self.file_path(draft_id, kind).write_bytes(data)

    def read_file(self, draft_id: str, kind: str) -> bytes | None:
        path = self.file_path(draft_id, kind)
        return path.read_bytes() if path.exists() else None

    def remove_file(self, draft_id: str, kind: str) -> None:
        self.file_path(draft_id, kind).unlink(missing_ok=True)

    # ------------------------------------------------------------------ rétention
    def purge_older_than(self, days: int) -> int:
        limit = time.time() - days * 86400
        removed = 0
        for path in self.root.glob("*/draft.json"):
            if path.stat().st_mtime < limit:
                shutil.rmtree(path.parent, ignore_errors=True)
                removed += 1
        return removed
