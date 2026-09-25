"""Application web « Dossier client » : prépare et crée un dossier client dans Salesforce."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .config import BASE_DIR, Settings, load_settings
from .drafts import DOC_KINDS, DraftLocked, DraftNotFound, DraftStore
from .extraction import DocumentError, analyse_document
from .salesforce import MockSalesforce, SalesforceAuthError, SalesforceClient, SalesforceError, ValidationError, fold, build_plan, validate
from .submission import SubmissionBusy, submit_draft

STATIC_DIR = Path(__file__).resolve().parent / "static"
SAMPLES = {"rc": BASE_DIR / "samples" / "RC_exemple_fictif.pdf",
           "fiscal": BASE_DIR / "samples" / "Attestation_fiscale_exemple_fictif.pdf"}
DOC_LABELS = {"rc": "RC", "fiscal": "Document fiscal"}
COMPARED_FIELDS = ["company_name", "rc_number", "ice", "tax_id"]
COMPANY_FIELDS = ["company_name", "common_name", "rc_number", "legal_form", "ice", "tax_id", "address", "city", "postal_code", "country"]
HINT_FIELDS = ["manager_name", "activity", "capital"]


def create_app(settings: Settings | None = None, sf_transport: httpx.BaseTransport | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = DraftStore(settings.data_dir)
    store.purge_older_than(settings.draft_retention_days)
    tokens = auth.TokenStore()
    mock_sf = None if settings.is_live else MockSalesforce(settings.mapping)

    app = FastAPI(title="Dossier client — préparation Salesforce", docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, https_only=settings.cookie_secure,
                       same_site="lax", session_cookie="dossier_session", max_age=auth.SESSION_TTL)
    app.state.settings, app.state.store, app.state.tokens, app.state.mock_sf = settings, store, tokens, mock_sf

    # ------------------------------------------------------------------ erreurs
    @app.exception_handler(ValidationError)
    async def _validation(_, exc: ValidationError):
        return JSONResponse({"errors": exc.messages}, status_code=422)

    @app.exception_handler(SalesforceAuthError)
    async def _sf_auth(_, exc: SalesforceAuthError):
        return JSONResponse({"errors": exc.messages, "login": True}, status_code=401)

    @app.exception_handler(SalesforceError)
    async def _sf(_, exc: SalesforceError):
        return JSONResponse({"errors": exc.messages}, status_code=502)

    @app.exception_handler(httpx.TransportError)
    async def _net(_, exc):
        return JSONResponse({"errors": ["Salesforce ne répond pas. Réessayez dans un instant."]}, status_code=504)

    @app.exception_handler(DraftNotFound)
    async def _nf(_, exc):
        return JSONResponse({"errors": ["Dossier introuvable."]}, status_code=404)

    @app.exception_handler(DraftLocked)
    async def _locked(_, exc: DraftLocked):
        return JSONResponse({"errors": [str(exc)]}, status_code=409)

    @app.exception_handler(SubmissionBusy)
    async def _busy(_, exc: SubmissionBusy):
        return JSONResponse({"errors": [str(exc)]}, status_code=409)

    @app.exception_handler(DocumentError)
    async def _doc(_, exc: DocumentError):
        return JSONResponse({"errors": [str(exc)]}, status_code=400)

    # ------------------------------------------------------------------ dépendances
    def current_user(request: Request) -> dict:
        if not settings.is_live:
            return {"id": "demo", "name": "Commercial (démo)"}
        data = tokens.get(request.session.get("sid"))
        if not data:
            raise HTTPException(401, detail="login")
        return {"id": data["user_id"], "name": data["display_name"]}

    def salesforce(request: Request, user: dict = Depends(current_user)):
        if not settings.is_live:
            return mock_sf
        sid = request.session.get("sid")
        data = tokens.get(sid)

        def _refresh():
            token = auth.refresh_access_token(settings, data.get("refresh_token"), transport=sf_transport)
            if token:
                tokens.update_token(sid, token)
            return token

        return SalesforceClient(data["instance_url"], data["access_token"], settings.mapping,
                                refresh=_refresh, transport=sf_transport)

    def same_origin(x_requested_with: str | None = Header(default=None)):
        # Protection CSRF : un formulaire d'un autre site ne peut pas ajouter cet en-tête.
        if x_requested_with != "dossier-client":
            raise HTTPException(403, detail="En-tête X-Requested-With manquant.")

    @app.exception_handler(HTTPException)
    async def _http(_, exc: HTTPException):
        if exc.status_code == 401:
            return JSONResponse({"errors": ["Connexion Salesforce requise."], "login": True}, status_code=401)
        return JSONResponse({"errors": [str(exc.detail)]}, status_code=exc.status_code)

    # ------------------------------------------------------------------ pages & auth
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "mode": settings.app_mode}

    @app.get("/auth/login", include_in_schema=False)
    def login():
        if not settings.is_live:
            return RedirectResponse("/")
        state, verifier = tokens.new_state()
        return RedirectResponse(auth.authorize_url(settings, state, verifier))

    @app.get("/auth/callback", include_in_schema=False)
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/?login_error=1")
        verifier = tokens.pop_state(state)
        if not verifier:
            return RedirectResponse("/?login_error=1")
        try:
            data = auth.exchange_code(settings, code, verifier, transport=sf_transport)
        except (httpx.HTTPError, KeyError, ValueError):
            return RedirectResponse("/?login_error=1")
        tokens.drop(request.session.get("sid"))
        request.session["sid"] = tokens.put(data)
        return RedirectResponse("/")

    @app.post("/auth/logout", dependencies=[Depends(same_origin)])
    def logout(request: Request):
        tokens.drop(request.session.pop("sid", None))
        return {"ok": True}

    # ------------------------------------------------------------------ configuration
    @app.get("/api/config")
    def config(request: Request):
        user = None
        if settings.is_live:
            data = tokens.get(request.session.get("sid"))
            user = {"name": data["display_name"]} if data else None
        else:
            user = {"name": "Commercial (démo)"}
        m = settings.mapping
        return {
            "mode": settings.app_mode,
            "user": user,
            "demo_samples": settings.demo_samples,
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "account_fields_sent": {k: bool(v) for k, v in m["account"]["fields"].items()},
            "segment": {"enabled": bool(m.get("segment", {}).get("enabled")),
                        "fields": [{k: f.get(k) for k in ("key", "label", "type", "required", "options")}
                                   for f in m.get("segment", {}).get("fields", [])]},
            "opportunity": {"enabled": bool(m.get("opportunity", {}).get("enabled")),
                            "contact_role": bool(m.get("opportunity", {}).get("contact_role", {}).get("enabled"))},
        }

    # ------------------------------------------------------------------ dossiers
    def public(draft: dict) -> dict:
        return {k: v for k, v in draft.items() if k != "owner"}

    @app.get("/api/drafts")
    def list_drafts(user: dict = Depends(current_user)):
        return store.list(user["id"])

    @app.post("/api/drafts", dependencies=[Depends(same_origin)])
    def create_draft(user: dict = Depends(current_user)):
        return public(store.create(user["id"]))

    @app.get("/api/drafts/{draft_id}")
    def get_draft(draft_id: str, user: dict = Depends(current_user)):
        return public(store.get(draft_id, user["id"]))

    @app.put("/api/drafts/{draft_id}", dependencies=[Depends(same_origin)])
    async def update_draft(draft_id: str, request: Request, user: dict = Depends(current_user)):
        changes = await request.json()
        if not isinstance(changes, dict):
            raise HTTPException(400, detail="Format invalide.")
        with store.lock(draft_id):
            draft = store.get(draft_id, user["id"])
            return public(store.update_sections(draft, changes))

    @app.delete("/api/drafts/{draft_id}", dependencies=[Depends(same_origin)])
    def delete_draft(draft_id: str, user: dict = Depends(current_user)):
        draft = store.get(draft_id, user["id"])
        if draft["status"] in {"submitting", "records_created"}:
            raise DraftLocked("Des fiches ont déjà été créées : terminez l'envoi des pièces jointes avant de supprimer.")
        store.delete(draft_id)
        return {"ok": True}

    # ------------------------------------------------------------------ documents
    def _attach(draft: dict, kind: str, filename: str, data: bytes) -> dict:
        if kind not in DOC_KINDS:
            raise HTTPException(404, detail="Type de document inconnu.")
        if draft["status"] not in {"draft", "error"}:
            raise DraftLocked("Ce dossier a déjà été envoyé dans Salesforce.")
        analysis = analyse_document(data, DOC_LABELS[kind], settings.max_upload_bytes, settings.max_pdf_pages)
        store.store_file(draft["id"], kind, data)
        safe_name = re.sub(r"[^\w.\- ]", "_", filename or f"{kind}.pdf")[:120]
        draft["documents"][kind] = {"filename": safe_name, "size": len(data), "pages": analysis["pages"],
                                    "text_found": analysis["text_found"], "warning": analysis["warning"],
                                    "fields": analysis["fields"], "uploaded": False}
        _merge_suggestions(draft)
        return analysis

    def _merge_suggestions(draft: dict) -> None:
        """Remplit les champs encore vides (jamais ceux déjà saisis) et signale les incohérences."""
        company, sources = draft["company"], draft["company_sources"]
        for kind in ("rc", "fiscal"):  # le RC est prioritaire
            for key, hit in draft["documents"].get(kind, {}).get("fields", {}).items():
                if key in HINT_FIELDS:
                    draft["hints"].setdefault(key, hit)
                elif key in COMPANY_FIELDS and not (company.get(key) or "").strip():
                    company[key] = hit["value"]
                    sources[key] = hit
        conflicts = []
        rc_f = draft["documents"].get("rc", {}).get("fields", {})
        fi_f = draft["documents"].get("fiscal", {}).get("fields", {})
        for key in COMPARED_FIELDS:
            a, b = rc_f.get(key, {}).get("value"), fi_f.get(key, {}).get("value")
            if a and b and _comparable(a) != _comparable(b) and not (_comparable(a) in _comparable(b) or _comparable(b) in _comparable(a)):
                conflicts.append({"field": key, "rc": a, "fiscal": b})
        draft["conflicts"] = conflicts

    def _comparable(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", fold(value))

    @app.post("/api/drafts/{draft_id}/documents/{kind}", dependencies=[Depends(same_origin)])
    async def upload_document(draft_id: str, kind: str, file: UploadFile = File(...), user: dict = Depends(current_user)):
        data = await file.read(settings.max_upload_bytes + 1)
        with store.lock(draft_id):
            draft = store.get(draft_id, user["id"])
            _attach(draft, kind, file.filename or "", data)
            return public(store.save(draft))

    @app.post("/api/drafts/{draft_id}/documents/{kind}/sample", dependencies=[Depends(same_origin)])
    def upload_sample(draft_id: str, kind: str, user: dict = Depends(current_user)):
        if not settings.demo_samples or kind not in SAMPLES:
            raise HTTPException(404, detail="Exemple indisponible.")
        with store.lock(draft_id):
            draft = store.get(draft_id, user["id"])
            _attach(draft, kind, SAMPLES[kind].name, SAMPLES[kind].read_bytes())
            return public(store.save(draft))

    @app.delete("/api/drafts/{draft_id}/documents/{kind}", dependencies=[Depends(same_origin)])
    def remove_document(draft_id: str, kind: str, user: dict = Depends(current_user)):
        with store.lock(draft_id):
            draft = store.get(draft_id, user["id"])
            if draft["status"] not in {"draft", "error"}:
                raise DraftLocked("Ce dossier a déjà été envoyé dans Salesforce.")
            draft["documents"].pop(kind, None)
            store.remove_file(draft_id, kind)
            label = DOC_LABELS.get(kind)
            draft["company_sources"] = {k: v for k, v in draft["company_sources"].items() if v.get("source") != label}
            draft["hints"] = {k: v for k, v in draft["hints"].items() if v.get("source") != label}
            _merge_suggestions(draft)
            return public(store.save(draft))

    # ------------------------------------------------------------------ Salesforce
    @app.get("/api/salesforce/accounts")
    def search_accounts(company_name: str = "", rc_number: str = "", ice: str = "", sf=Depends(salesforce)):
        criteria = {"company_name": company_name.strip(), "rc_number": rc_number.strip(), "ice": ice.strip()}
        return sf.search_accounts(criteria)

    @app.get("/api/salesforce/accounts/{account_id}/contacts")
    def account_contacts(account_id: str, sf=Depends(salesforce)):
        return sf.list_contacts(account_id)

    @app.get("/api/salesforce/check-mapping")
    def check_mapping(sf=Depends(salesforce)):
        return sf.check_mapping()

    @app.get("/api/drafts/{draft_id}/preview")
    def preview(draft_id: str, user: dict = Depends(current_user)):
        draft = store.get(draft_id, user["id"])
        return {"errors": validate(draft, settings.mapping), "summary": build_plan(draft, settings.mapping)["summary"]}

    @app.post("/api/drafts/{draft_id}/submit", dependencies=[Depends(same_origin)])
    def submit(draft_id: str, user: dict = Depends(current_user), sf=Depends(salesforce)):
        return public(submit_draft(draft_id, user["id"], sf, store, settings.mapping))

    return app

