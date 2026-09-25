"""Communication avec Salesforce Sales Cloud.

- ``build_plan`` : transforme un dossier validé en requête « Composite » (tout ou rien) ;
- ``SalesforceClient`` : appels REST réels avec le jeton OAuth de l'utilisateur connecté ;
- ``MockSalesforce`` : org simulée en mémoire, pour la démonstration.

Le commercial reste propriétaire des enregistrements : en mode live, les appels sont faits avec
SON jeton, donc avec ses droits, ses règles de validation et ses règles de doublons.
"""

from __future__ import annotations

import base64
import itertools
import re
import unicodedata
from datetime import date
from typing import Callable

import httpx

SF_ID = re.compile(r"^[a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STOPWORDS = {"sarl", "au", "sa", "sas", "snc", "ste", "societe", "sté", "et", "de", "des", "du", "la", "le", "les", "cie"}

FRIENDLY_ERRORS = {
    "DUPLICATES_DETECTED": "Salesforce a détecté un doublon possible. Recherchez le compte existant et réutilisez-le.",
    "REQUIRED_FIELD_MISSING": "Un champ obligatoire dans Salesforce n'est pas rempli.",
    "INVALID_FIELD": "Un champ configuré n'existe pas dans Salesforce (correspondance des champs à corriger).",
    "INVALID_FIELD_FOR_INSERT_UPDATE": "Vous n'avez pas le droit de renseigner l'un des champs envoyés.",
    "FIELD_CUSTOM_VALIDATION_EXCEPTION": "Une règle de validation Salesforce a refusé l'enregistrement.",
    "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST": "Une valeur de liste n'est pas autorisée dans Salesforce.",
    "INSUFFICIENT_ACCESS_OR_READONLY": "Droits insuffisants dans Salesforce pour cette opération.",
    "STRING_TOO_LONG": "Une valeur est trop longue pour le champ Salesforce.",
}


class SalesforceError(RuntimeError):
    def __init__(self, messages: list[str], status: int | None = None, codes: list[str] | None = None):
        super().__init__("; ".join(messages))
        self.messages = messages
        self.status = status
        self.codes = codes or []


class SalesforceAuthError(SalesforceError):
    """Jeton expiré ou révoqué : l'utilisateur doit se reconnecter."""


class ValidationError(ValueError):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


# =========================================================================== utilitaires

def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


def name_keyword(name: str) -> str | None:
    """Mot le plus significatif d'une raison sociale (pour chercher les comptes proches)."""
    words = [w for w in re.findall(r"[\w']+", name or "") if len(w) >= 3 and fold(w) not in STOPWORDS]
    return max(words, key=len) if words else None


def soql_quote(value: str, like: bool = False) -> str:
    value = value.replace("\\", "\\\\").replace("'", "\\'")
    if like:
        value = value.replace("%", "\\%").replace("_", "\\_")
    return value


def map_fields(values: dict, field_map: dict, value_maps: dict | None = None) -> dict:
    out = {}
    for app_key, sf_field in field_map.items():
        if not sf_field:
            continue
        value = values.get(app_key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        value = value.strip() if isinstance(value, str) else value
        translate = (value_maps or {}).get(app_key)
        if isinstance(translate, dict):
            value = translate.get(value, value)
        out[sf_field] = value
    return out


def record_url(instance_url: str, sobject: str, record_id: str) -> str:
    return f"{instance_url}/lightning/r/{sobject}/{record_id}/view"


# =========================================================================== validation & plan

def validate(draft: dict, mapping: dict) -> list[str]:
    errors: list[str] = []
    company, contact = draft.get("company", {}), draft.get("contact", {})
    acc_choice, con_choice = draft.get("account_choice", {}), draft.get("contact_choice", {})

    if not draft.get("verified"):
        errors.append("Cochez la case confirmant que les pièces concernent le bon client et que les informations sont vérifiées.")

    if acc_choice.get("mode") == "existing":
        if not SF_ID.match(acc_choice.get("id") or ""):
            errors.append("Compte existant : identifiant Salesforce invalide.")
    else:
        def val(key: str) -> str:
            return str(company.get(key) or "").strip()

        for key, label in (("company_name", "Nom du compte"), ("sole_proprietorship", "Entreprise individuelle"),
                           ("address", "Adresse de facturation"), ("postal_code", "Code postal de facturation"),
                           ("city", "Ville de facturation")):
            if not val(key):
                errors.append(f"« {label} » obligatoire.")
        if mapping["account"]["fields"].get("rc_number") and not val("rc_number"):
            errors.append("« Numéro de RC » obligatoire.")
        if val("sole_proprietorship") and val("sole_proprietorship") not in {"Oui", "Non"}:
            errors.append("« Entreprise individuelle » : choisissez Oui ou Non.")
        if val("postal_code") and not re.fullmatch(r"\d{5}", val("postal_code")):
            errors.append("Le code postal marocain comporte 5 chiffres.")
        if val("ice") and not re.fullmatch(r"\d{15}", val("ice")):
            errors.append("Le numéro ICE doit comporter 15 chiffres.")
        if val("tax_id") and not val("tax_id_type"):
            errors.append("Choisissez le « Type de numéro d'identification fiscale ».")
        if val("tax_id_type") and not val("tax_id"):
            errors.append("« Numéro d'identification fiscale » manquant pour le type choisi.")
        seg = mapping.get("segment", {})
        if seg.get("enabled"):
            for f in seg.get("fields", []):
                if f.get("required") and not str(draft.get("segment", {}).get(f["key"], "")).strip():
                    errors.append(f"Segmentation : « {f['label']} » obligatoire.")

    if con_choice.get("mode") == "existing":
        if acc_choice.get("mode") != "existing":
            errors.append("Un contact existant ne peut être choisi que pour un compte existant.")
        elif not SF_ID.match(con_choice.get("id") or ""):
            errors.append("Contact existant : identifiant Salesforce invalide.")
    else:
        if not (contact.get("last_name") or "").strip():
            errors.append("Nom du contact obligatoire.")
        if not ((contact.get("mobile") or "").strip() or (contact.get("email") or "").strip()):
            errors.append("Renseignez au moins le téléphone ou l'e-mail du contact.")
        email = (contact.get("email") or "").strip()
        if email and not EMAIL.match(email):
            errors.append("Adresse e-mail du contact invalide.")

    opp_cfg = mapping.get("opportunity", {})
    if opp_cfg.get("enabled"):
        opp = draft.get("opportunity", {})
        if not (opp.get("name") or "").strip():
            errors.append("Nom de l'opportunité obligatoire.")
        try:
            date.fromisoformat(opp.get("close_date") or "")
        except ValueError:
            errors.append("Date de clôture estimée de l'opportunité obligatoire (AAAA-MM-JJ).")
    return errors


def build_plan(draft: dict, mapping: dict) -> dict:
    """Construit les sous-requêtes Composite et un résumé lisible de ce qui sera créé/réutilisé."""
    version = mapping["api_version"]
    base = f"/services/data/{version}/sobjects"
    subrequests: list[dict] = []
    summary: list[str] = []

    acc_cfg, con_cfg = mapping["account"], mapping["contact"]
    seg_cfg, opp_cfg = mapping.get("segment", {}), mapping.get("opportunity", {})

    # --- Compte
    if draft["account_choice"].get("mode") == "existing":
        account_ref = draft["account_choice"]["id"]
        summary.append(f"Compte réutilisé (non modifié) : {draft['account_choice'].get('name') or account_ref}")
    else:
        body = {**acc_cfg.get("defaults", {}), **map_fields(draft["company"], acc_cfg["fields"], acc_cfg.get("value_maps"))}
        subrequests.append({"method": "POST", "url": f"{base}/{acc_cfg['sobject']}", "referenceId": "account", "body": body})
        account_ref = "@{account.id}"
        summary.append(f"Nouveau compte : {draft['company'].get('company_name')}")

        # --- Segmentation (seulement pour un nouveau compte)
        if seg_cfg.get("enabled"):
            seg_map = {f["key"]: f["sf_field"] for f in seg_cfg.get("fields", [])}
            seg_body = {**seg_cfg.get("defaults", {}), **map_fields(draft.get("segment", {}), seg_map),
                        seg_cfg["account_lookup_field"]: account_ref}
            subrequests.append({"method": "POST", "url": f"{base}/{seg_cfg['sobject']}", "referenceId": "segment", "body": seg_body})
            summary.append("Nouvelle segmentation rattachée au compte")

    # --- Contact
    if draft["contact_choice"].get("mode") == "existing":
        contact_ref = draft["contact_choice"]["id"]
        summary.append(f"Contact réutilisé : {draft['contact_choice'].get('name') or contact_ref}")
    else:
        body = {**con_cfg.get("defaults", {}), **map_fields(draft["contact"], con_cfg["fields"]), "AccountId": account_ref}
        subrequests.append({"method": "POST", "url": f"{base}/{con_cfg['sobject']}", "referenceId": "contact", "body": body})
        contact_ref = "@{contact.id}"
        name = f"{draft['contact'].get('first_name', '')} {draft['contact'].get('last_name', '')}".strip()
        summary.append(f"Nouveau contact : {name}")

    # --- Opportunité + rôle du contact
    if opp_cfg.get("enabled"):
        opp = dict(draft.get("opportunity", {}))
        if opp.get("quantity") and not opp_cfg["fields"].get("quantity"):
            extra = f"Quantité envisagée : {opp['quantity']}"
            opp["description"] = f"{opp.get('description', '').strip()}\n{extra}".strip()
        body = {**opp_cfg.get("defaults", {}), **map_fields(opp, opp_cfg["fields"]), "AccountId": account_ref}
        subrequests.append({"method": "POST", "url": f"{base}/{opp_cfg['sobject']}", "referenceId": "opportunity", "body": body})
        summary.append(f"Nouvelle opportunité : {opp.get('name')}")

        role_cfg = opp_cfg.get("contact_role", {})
        if role_cfg.get("enabled"):
            subrequests.append({
                "method": "POST", "url": f"{base}/OpportunityContactRole", "referenceId": "contactRole",
                "body": {"OpportunityId": "@{opportunity.id}", "ContactId": contact_ref,
                         "Role": role_cfg.get("role"), "IsPrimary": bool(role_cfg.get("is_primary", True))},
            })
            summary.append("Contact rattaché à l'opportunité (contact principal)")

    docs = [k for k, d in draft.get("documents", {}).items() if not d.get("uploaded")]
    for kind in docs:
        summary.append(f"Pièce jointe : {mapping['files']['titles'].get(kind, kind)}")

    return {
        "subrequests": subrequests,
        "summary": summary,
        "account_id": None if account_ref.startswith("@") else account_ref,
        "contact_id": None if contact_ref.startswith("@") else contact_ref,
    }


def composite_errors(response: dict) -> tuple[list[str], list[str]]:
    messages, codes = [], []
    for item in response.get("compositeResponse", []):
        if item.get("httpStatusCode", 200) < 300:
            continue
        for err in item.get("body") or []:
            code = err.get("errorCode", "")
            if code == "PROCESSING_HALTED":
                continue
            codes.append(code)
            friendly = FRIENDLY_ERRORS.get(code)
            detail = err.get("message", "")
            fields = ", ".join(err.get("fields") or [])
            text = f"[{item.get('referenceId')}] {friendly + ' ' if friendly else ''}{detail}"
            messages.append(text + (f" (champs : {fields})" if fields else ""))
    return messages or ["Salesforce a refusé l'enregistrement (aucun détail fourni)."], codes


# =========================================================================== client réel

class SalesforceClient:
    def __init__(self, instance_url: str, access_token: str, mapping: dict,
                 refresh: Callable[[], str | None] | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.instance_url = instance_url.rstrip("/")
        self.mapping = mapping
        self._token = access_token
        self._refresh = refresh
        self._http = httpx.Client(base_url=self.instance_url, timeout=30.0, transport=transport)

    @property
    def base(self) -> str:
        return f"/services/data/{self.mapping['api_version']}"

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
            resp = self._http.request(method, path, headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 1 and self._refresh:
                new_token = self._refresh()
                if new_token:
                    self._token = new_token
                    continue
            if resp.status_code == 401:
                raise SalesforceAuthError(["Session Salesforce expirée : reconnectez-vous."], 401)
            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = []
                errors = payload if isinstance(payload, list) else [payload]
                codes = [e.get("errorCode", "") for e in errors if isinstance(e, dict)]
                msgs = [FRIENDLY_ERRORS.get(e.get("errorCode", ""), "") + " " + e.get("message", "")
                        for e in errors if isinstance(e, dict)]
                raise SalesforceError([m.strip() for m in msgs if m.strip()] or [f"Erreur Salesforce HTTP {resp.status_code}"],
                                      resp.status_code, codes)
            return resp
        raise SalesforceAuthError(["Session Salesforce expirée : reconnectez-vous."], 401)

    # -------------------------------------------------------------- lecture
    def query(self, soql: str) -> list[dict]:
        resp = self._request("GET", f"{self.base}/query", params={"q": soql})
        return resp.json().get("records", [])

    def search_accounts(self, criteria: dict) -> list[dict]:
        fields = self.mapping["account"]["fields"]
        rc_f, ice_f, city_f = fields.get("rc_number"), fields.get("ice"), fields.get("city")
        select = ["Id", "Name"] + [f for f in (rc_f, ice_f, city_f) if f]
        where = []
        if rc_f and criteria.get("rc_number"):
            where.append(f"{rc_f} = '{soql_quote(criteria['rc_number'])}'")
        if ice_f and criteria.get("ice"):
            where.append(f"{ice_f} = '{soql_quote(criteria['ice'])}'")
        keyword = name_keyword(criteria.get("company_name") or "")
        if keyword:
            where.append(f"Name LIKE '%{soql_quote(keyword, like=True)}%'")
        if not where:
            return []
        soql = f"SELECT {', '.join(dict.fromkeys(select))} FROM {self.mapping['account']['sobject']} " \
               f"WHERE {' OR '.join(where)} ORDER BY LastModifiedDate DESC LIMIT 10"
        rows = self.query(soql)
        return [describe_match({"id": r["Id"], "name": r.get("Name"),
                                "rc_number": r.get(rc_f) if rc_f else None,
                                "ice": r.get(ice_f) if ice_f else None,
                                "city": r.get(city_f) if city_f else None}, criteria) for r in rows]

    def list_contacts(self, account_id: str) -> list[dict]:
        if not SF_ID.match(account_id):
            raise SalesforceError(["Identifiant de compte invalide."], 400)
        f = self.mapping["contact"]["fields"]
        cols = [c for c in (f.get("first_name"), f.get("last_name"), f.get("title"), f.get("mobile"), f.get("email")) if c]
        soql = f"SELECT Id, {', '.join(dict.fromkeys(cols))} FROM {self.mapping['contact']['sobject']} " \
               f"WHERE AccountId = '{account_id}' ORDER BY LastName LIMIT 50"
        return [{"id": r["Id"], **{k: r.get(v) for k, v in f.items() if v}} for r in self.query(soql)]

    # -------------------------------------------------------------- écriture
    def create_records(self, subrequests: list[dict]) -> dict[str, str]:
        if not subrequests:
            return {}
        resp = self._request("POST", f"{self.base}/composite",
                             json={"allOrNone": True, "compositeRequest": subrequests})
        payload = resp.json()
        items = payload.get("compositeResponse", [])
        if any(i.get("httpStatusCode", 500) >= 300 for i in items):
            messages, codes = composite_errors(payload)
            raise SalesforceError(messages, 400, codes)
        return {i["referenceId"]: i["body"]["id"] for i in items}

    def upload_file(self, filename: str, title: str, data: bytes, parent_id: str, extra_links: list[str]) -> str:
        resp = self._request("POST", f"{self.base}/sobjects/ContentVersion", json={
            "Title": title, "PathOnClient": filename,
            "VersionData": base64.b64encode(data).decode("ascii"),
            "FirstPublishLocationId": parent_id,
        })
        version_id = resp.json()["id"]
        if extra_links:
            rows = self.query(f"SELECT ContentDocumentId FROM ContentVersion WHERE Id = '{version_id}'")
            doc_id = rows[0]["ContentDocumentId"]
            for linked in extra_links:
                self._request("POST", f"{self.base}/sobjects/ContentDocumentLink",
                              json={"ContentDocumentId": doc_id, "LinkedEntityId": linked, "ShareType": "V"})
        return version_id

    # -------------------------------------------------------------- diagnostic
    def check_mapping(self) -> dict:
        report: dict[str, dict] = {}
        m = self.mapping
        sections = {"account": (m["account"]["sobject"], list(m["account"]["fields"].values()), m["account"].get("defaults", {})),
                    "contact": (m["contact"]["sobject"], list(m["contact"]["fields"].values()) + ["AccountId"], m["contact"].get("defaults", {}))}
        if m.get("segment", {}).get("enabled"):
            s = m["segment"]
            sections["segment"] = (s["sobject"], [f["sf_field"] for f in s["fields"]] + [s["account_lookup_field"]], s.get("defaults", {}))
        if m.get("opportunity", {}).get("enabled"):
            o = m["opportunity"]
            sections["opportunity"] = (o["sobject"], list(o["fields"].values()) + ["AccountId"], o.get("defaults", {}))
        for key, (sobject, mapped, defaults) in sections.items():
            mapped = [f for f in mapped if f] + list(defaults)
            try:
                desc = self._request("GET", f"{self.base}/sobjects/{sobject}/describe").json()
            except SalesforceError as exc:
                report[key] = {"sobject": sobject, "ok": False, "error": exc.messages}
                continue
            fields = {f["name"]: f for f in desc.get("fields", [])}
            missing = [f for f in mapped if f not in fields]
            not_createable = [f for f in mapped if f in fields and not fields[f].get("createable")]
            required = [n for n, f in fields.items()
                        if f.get("createable") and not f.get("nillable") and not f.get("defaultedOnCreate")
                        and f.get("type") != "boolean" and n not in mapped]
            report[key] = {"sobject": sobject, "ok": not (missing or not_createable or required),
                           "missing_fields": missing, "not_createable": not_createable,
                           "required_not_mapped": required}
        return report


def describe_match(account: dict, criteria: dict) -> dict:
    reasons = []
    if criteria.get("rc_number") and account.get("rc_number") == criteria["rc_number"]:
        reasons.append("même numéro RC")
    if criteria.get("ice") and account.get("ice") == criteria["ice"]:
        reasons.append("même ICE")
    if not reasons:
        reasons.append("nom proche")
    account["match"] = ", ".join(reasons)
    account["strong"] = reasons != ["nom proche"]
    return account


# =========================================================================== org simulée

class MockSalesforce:
    """Org Salesforce simulée (mode démonstration). Données 100 % fictives."""

    instance_url = "https://demo.invalid"
    PREFIX = {"Account": "001", "Contact": "003", "Opportunity": "006", "OpportunityContactRole": "00K",
              "ContentVersion": "068", "ContentDocumentLink": "06A"}

    def __init__(self, mapping: dict):
        self.mapping = mapping
        self.records: dict[str, dict[str, dict]] = {}
        self._seq = itertools.count(1)
        acc = self._insert("Account", {"Name": "ATLAS DEMO TRANSPORT", "_rc": "55555", "_ice": "001122334455667", "_city": "Démoville"})
        self._insert("Contact", {"FirstName": "Salma", "LastName": "EXEMPLE", "Title": "Responsable achats",
                                 "MobilePhone": "+212 600 000 001", "Email": "salma.exemple@example.com", "AccountId": acc})
        self._insert("Account", {"Name": "TRANSPORTS FICTIFS DU NORD SARL", "_rc": "12345", "_ice": "009999999999999", "_city": "Exempleville"})

    def _insert(self, sobject: str, body: dict) -> str:
        prefix = self.PREFIX.get(sobject, "a0X")
        record_id = f"{prefix}MOCK{next(self._seq):011d}"
        self.records.setdefault(sobject, {})[record_id] = {"Id": record_id, **body}
        return record_id

    def search_accounts(self, criteria: dict) -> list[dict]:
        f = self.mapping["account"]["fields"]
        keyword = name_keyword(criteria.get("company_name") or "")
        out = []
        for r in self.records.get("Account", {}).values():
            rc = r.get("_rc") or r.get(f.get("rc_number") or "")
            ice = r.get("_ice") or r.get(f.get("ice") or "")
            hit = (criteria.get("rc_number") and rc == criteria["rc_number"]) or \
                  (criteria.get("ice") and ice == criteria["ice"]) or \
                  (keyword and fold(keyword) in fold(r.get("Name", "")))
            if hit:
                out.append(describe_match({"id": r["Id"], "name": r["Name"], "rc_number": rc, "ice": ice,
                                           "city": r.get("_city") or r.get(f.get("city") or "")}, criteria))
        return out[:10]

    def list_contacts(self, account_id: str) -> list[dict]:
        f = self.mapping["contact"]["fields"]
        return [{"id": r["Id"], **{k: r.get(v) for k, v in f.items() if v}}
                for r in self.records.get("Contact", {}).values() if r.get("AccountId") == account_id]

    def create_records(self, subrequests: list[dict]) -> dict[str, str]:
        refs: dict[str, str] = {}
        for sub in subrequests:
            sobject = sub["url"].rsplit("/", 1)[-1]
            body = {k: (refs[v[2:-4]] if isinstance(v, str) and v.startswith("@{") else v) for k, v in sub["body"].items()}
            refs[sub["referenceId"]] = self._insert(sobject, body)
        return refs

    def upload_file(self, filename: str, title: str, data: bytes, parent_id: str, extra_links: list[str]) -> str:
        version_id = self._insert("ContentVersion", {"Title": title, "PathOnClient": filename, "Size": len(data),
                                                      "FirstPublishLocationId": parent_id})
        for linked in extra_links:
            self._insert("ContentDocumentLink", {"ContentVersionId": version_id, "LinkedEntityId": linked})
        return version_id

    def check_mapping(self) -> dict:
        return {"demo": {"ok": True, "note": "Mode démonstration : la vérification réelle se fait en mode live."}}
