"""Mode live : OAuth + appels REST, contre une fausse API Salesforce (httpx.MockTransport)."""

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import HEADERS, make_settings

INSTANCE = "https://volvo-demo.my.salesforce.test"


class FakeSalesforce:
    def __init__(self):
        self.composite_mode = "ok"  # ok | duplicate | network
        self.accounts = []
        self.created = []
        self.uploads = []
        self.token = "token-1"
        self.expire_next = False
        self.queries = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.path == "/services/oauth2/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0]
                return httpx.Response(200, json={"access_token": self.token, "refresh_token": "r1",
                                                 "instance_url": INSTANCE, "id": "https://login.example.test/id/00D/005X"})
            self.token = "token-2"
            return httpx.Response(200, json={"access_token": self.token})
        if url.path == "/id/00D/005X":
            return httpx.Response(200, json={"user_id": "005X", "display_name": "Commercial Test"})

        if request.headers.get("Authorization") != f"Bearer {self.token}" or self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID", "message": "expired"}])

        if url.path.endswith("/query"):
            q = url.params["q"]
            self.queries.append(q)
            if "FROM ContentVersion" in q:
                return httpx.Response(200, json={"records": [{"ContentDocumentId": "069X"}]})
            if "FROM Account" in q:
                return httpx.Response(200, json={"records": self.accounts})
            return httpx.Response(200, json={"records": []})
        if url.path.endswith("/composite"):
            body = json.loads(request.content)
            assert body["allOrNone"] is True
            if self.composite_mode == "network":
                raise httpx.ReadTimeout("timeout", request=request)
            if self.composite_mode == "duplicate":
                return httpx.Response(200, json={"compositeResponse": [
                    {"referenceId": "account", "httpStatusCode": 400,
                     "body": [{"errorCode": "DUPLICATES_DETECTED", "message": "Use one of these records?"}]},
                    {"referenceId": "contact", "httpStatusCode": 400,
                     "body": [{"errorCode": "PROCESSING_HALTED", "message": "halted"}]},
                ]})
            ids = {"account": "001X", "segment": "a0XX", "contact": "003X", "opportunity": "006X", "contactRole": "00KX"}
            self.created.append(body["compositeRequest"])
            return httpx.Response(200, json={"compositeResponse": [
                {"referenceId": s["referenceId"], "httpStatusCode": 201, "body": {"id": ids[s["referenceId"]], "success": True}}
                for s in body["compositeRequest"]]})
        if url.path.endswith("/sobjects/ContentVersion"):
            self.uploads.append(json.loads(request.content))
            return httpx.Response(201, json={"id": f"068X{len(self.uploads)}"})
        return httpx.Response(404, json=[{"errorCode": "NOT_FOUND", "message": str(url)}])


@pytest.fixture
def live(tmp_path):
    fake = FakeSalesforce()
    app = create_app(make_settings(tmp_path, mode="live"), sf_transport=httpx.MockTransport(fake))
    client = TestClient(app)
    return client, fake


def login(client):
    resp = client.get("/auth/login", follow_redirects=False)
    target = urlparse(resp.headers["location"])
    params = parse_qs(target.query)
    assert target.netloc == "login.example.test"
    assert params["code_challenge_method"] == ["S256"] and params["client_id"] == ["client-id"]
    client.get("/auth/callback", params={"code": "abc", "state": params["state"][0]}, follow_redirects=False)


def ready_draft(client, upload=True):
    did = client.post("/api/drafts", headers=HEADERS).json()["id"]
    client.put(f"/api/drafts/{did}", headers=HEADERS, json={
        "company": {"company_name": "ATLAS DÉMO TRANS", "rc_number": "99001", "sole_proprietorship": "Non",
                    "address": "21 avenue de la Démonstration", "postal_code": "99100", "city": "Villetest"},
        "contact": {"last_name": "EXEMPLE", "email": "nadia@example.com"},
        "segment": {"segment_activity": "Transport de marchandises"},
        "opportunity": {"name": "Atlas - 3 camions", "close_date": "2026-12-31"},
        "verified": True,
    })
    if upload:
        client.post(f"/api/drafts/{did}/documents/rc/sample", headers=HEADERS)
    return did


def test_requires_login(live):
    client, _ = live
    assert client.get("/api/config").json()["user"] is None
    resp = client.get("/api/drafts")
    assert resp.status_code == 401 and resp.json()["login"] is True


def test_callback_rejects_unknown_state(live):
    client, _ = live
    resp = client.get("/auth/callback", params={"code": "abc", "state": "forged"}, follow_redirects=False)
    assert "login_error" in resp.headers["location"]
    assert client.get("/api/config").json()["user"] is None


def test_live_submit_success(live):
    client, fake = live
    login(client)
    assert client.get("/api/config").json()["user"]["name"] == "Commercial Test"
    did = ready_draft(client)
    result = client.post(f"/api/drafts/{did}/submit", headers=HEADERS).json()
    assert result["status"] == "completed"
    assert result["result"]["links"]["opportunity"] == f"{INSTANCE}/lightning/r/Opportunity/006X/view"
    assert fake.uploads[0]["FirstPublishLocationId"] == "001X"
    assert fake.uploads[0]["Title"] == "Registre de commerce - ATLAS DÉMO TRANS"


def test_live_duplicate_rule_leaves_nothing_half_created(live):
    client, fake = live
    login(client)
    fake.composite_mode = "duplicate"
    did = ready_draft(client)
    resp = client.post(f"/api/drafts/{did}/submit", headers=HEADERS)
    assert resp.status_code == 502
    assert "doublon" in resp.json()["errors"][0]
    draft = client.get(f"/api/drafts/{did}").json()
    assert draft["status"] == "error" and fake.uploads == []
    # le dossier reste modifiable pour choisir le compte existant
    assert client.put(f"/api/drafts/{did}", headers=HEADERS, json={"verified": True}).status_code == 200


def test_live_network_error_then_retry_checks_for_existing_account(live):
    client, fake = live
    login(client)
    fake.composite_mode = "network"
    did = ready_draft(client, upload=False)
    assert client.post(f"/api/drafts/{did}/submit", headers=HEADERS).status_code == 504
    assert client.get(f"/api/drafts/{did}").json()["status"] == "uncertain"

    # L'essai précédent avait en fait abouti : un compte au même RC existe désormais.
    fake.composite_mode = "ok"
    fake.accounts = [{"Id": "001X", "Name": "ATLAS DÉMO TRANS", "RC_Number__c": "99001"}]
    resp = client.post(f"/api/drafts/{did}/submit", headers=HEADERS)
    assert resp.status_code == 422 and "existe maintenant" in resp.json()["errors"][0]
    assert fake.created == []

    # Aucun compte trouvé : le nouvel essai est autorisé.
    fake.accounts = []
    assert client.post(f"/api/drafts/{did}/submit", headers=HEADERS).json()["status"] == "completed"


def test_expired_token_is_refreshed(live):
    client, fake = live
    login(client)
    fake.expire_next = True
    assert client.get("/api/salesforce/accounts", params={"company_name": "Atlas"}).status_code == 200
    assert fake.token == "token-2"


def test_search_query_is_escaped(live):
    client, fake = live
    login(client)
    client.get("/api/salesforce/accounts", params={"company_name": "O'Hara_Trans", "rc_number": "1' OR Name!='"})
    assert "RC_Number__c = '1\\' OR Name!=\\''" in fake.queries[-1]
    assert "Name LIKE '%O\\'Hara\\_Trans%'" in fake.queries[-1]
