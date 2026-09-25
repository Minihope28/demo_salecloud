"""Parcours complet en mode démonstration (Salesforce simulé)."""

from tests.conftest import HEADERS


def _prepared_draft(client):
    draft = client.post("/api/drafts", headers=HEADERS).json()
    did = draft["id"]
    draft = client.post(f"/api/drafts/{did}/documents/rc/sample", headers=HEADERS).json()
    draft = client.post(f"/api/drafts/{did}/documents/fiscal/sample", headers=HEADERS).json()
    return draft


def test_full_flow_creates_records_once(client):
    draft = _prepared_draft(client)
    did = draft["id"]

    # Les champs vides sont préremplis depuis les documents, avec leur source.
    assert draft["company"]["company_name"] == "ATLAS DÉMO TRANS"
    assert draft["company"]["ice"] == "009988776600055"
    assert draft["company_sources"]["rc_number"]["source"] == "RC"
    assert draft["hints"]["manager_name"]["value"] == "M. Karim EXEMPLE"
    assert draft["conflicts"] == []  # « ATLAS DÉMO TRANS » ⊂ « ATLAS DÉMO TRANS SARL AU »

    # Recherche de doublons : un compte au nom proche existe dans l'org de démo.
    matches = client.get("/api/salesforce/accounts", params={"company_name": "ATLAS DÉMO TRANS", "rc_number": "99001"}).json()
    assert matches and matches[0]["match"] == "nom proche" and matches[0]["strong"] is False

    client.put(f"/api/drafts/{did}", headers=HEADERS, json={
        "contact": {"first_name": "Nadia", "last_name": "EXEMPLE", "mobile": "+212600000000"},
        "segment": {"segment_activity": "Transport de marchandises"},
        "opportunity": {"name": "Atlas - 3 camions", "close_date": "2026-12-31"},
        "verified": True,
        "owner": "pirate",  # champ non modifiable : ignoré
    })
    preview = client.get(f"/api/drafts/{did}/preview").json()
    assert preview["errors"] == []
    assert any("Nouveau compte" in s for s in preview["summary"])

    result = client.post(f"/api/drafts/{did}/submit", headers=HEADERS).json()
    assert result["status"] == "completed"
    assert result["result"]["opportunity_id"].startswith("006")
    assert result["result"]["links"]["opportunity"].endswith("/view")
    assert all(d["uploaded"] for d in result["documents"].values())

    sf = client.app.state.mock_sf
    accounts_before = len(sf.records["Account"])
    account = sf.records["Account"][result["result"]["account_id"]]
    assert account["RC_Number__c"] == "99001" and account["Legal_Form__c"] == "SARL AU"
    assert len(sf.records["ContentVersion"]) == 2
    # PDF supprimés localement une fois envoyés
    assert client.app.state.store.read_file(did, "rc") is None

    # Un second clic ne recrée rien et le dossier n'est plus modifiable.
    again = client.post(f"/api/drafts/{did}/submit", headers=HEADERS).json()
    assert again["result"]["account_id"] == result["result"]["account_id"]
    assert len(sf.records["Account"]) == accounts_before
    assert client.put(f"/api/drafts/{did}", headers=HEADERS, json={"verified": False}).status_code == 409


def test_submit_refused_when_incomplete(client):
    did = client.post("/api/drafts", headers=HEADERS).json()["id"]
    resp = client.post(f"/api/drafts/{did}/submit", headers=HEADERS)
    assert resp.status_code == 422
    assert any("Raison sociale" in e for e in resp.json()["errors"])
    assert client.get(f"/api/drafts/{did}").json()["status"] == "draft"


def test_existing_account_flow(client):
    matches = client.get("/api/salesforce/accounts", params={"rc_number": "55555"}).json()
    assert matches[0]["strong"] is True and "même numéro RC" in matches[0]["match"]
    acc = matches[0]
    contacts = client.get(f"/api/salesforce/accounts/{acc['id']}/contacts").json()
    assert contacts[0]["last_name"] == "EXEMPLE"

    did = client.post("/api/drafts", headers=HEADERS).json()["id"]
    client.put(f"/api/drafts/{did}", headers=HEADERS, json={
        "account_choice": {"mode": "existing", "id": acc["id"], "name": acc["name"]},
        "contact_choice": {"mode": "existing", "id": contacts[0]["id"], "name": "Salma EXEMPLE"},
        "opportunity": {"name": "Renouvellement flotte", "close_date": "2026-11-30"},
        "verified": True,
    })
    sf = client.app.state.mock_sf
    before = len(sf.records["Account"])
    result = client.post(f"/api/drafts/{did}/submit", headers=HEADERS).json()
    assert result["status"] == "completed"
    assert result["result"]["account_id"] == acc["id"]
    assert len(sf.records["Account"]) == before


def test_csrf_header_required(client):
    assert client.post("/api/drafts").status_code == 403


def test_other_user_cannot_read_draft(client):
    store = client.app.state.store
    foreign = store.create("someone-else")
    assert client.get(f"/api/drafts/{foreign['id']}").status_code == 404


def test_upload_rejects_non_pdf(client):
    did = client.post("/api/drafts", headers=HEADERS).json()["id"]
    resp = client.post(f"/api/drafts/{did}/documents/rc", headers=HEADERS,
                       files={"file": ("rc.pdf", b"not a pdf", "application/pdf")})
    assert resp.status_code == 400


def test_remove_document_keeps_user_values(client):
    draft = _prepared_draft(client)
    did = draft["id"]
    client.put(f"/api/drafts/{did}", headers=HEADERS, json={"company": {**draft["company"], "city": "Démoville"}})
    after = client.delete(f"/api/drafts/{did}/documents/rc", headers=HEADERS).json()
    assert "rc" not in after["documents"]
    assert after["company"]["city"] == "Démoville"
    assert all(s["source"] != "RC" for s in after["company_sources"].values())
