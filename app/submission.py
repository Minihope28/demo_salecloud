"""Envoi d'un dossier vers Salesforce, sans créer de doublon en cas de nouvel essai.

Étapes :
1. création du compte, de la segmentation, du contact, de l'opportunité et du rôle en UNE requête
   Composite « tout ou rien » : soit tout est créé, soit rien ;
2. envoi des pièces jointes (RC, document fiscal) sur le compte ;
3. chaque étape réussie est enregistrée dans le dossier : un nouvel essai reprend là où ça s'est arrêté.
"""

from __future__ import annotations

import httpx

from .drafts import DraftStore
from .salesforce import SalesforceAuthError, SalesforceError, ValidationError, build_plan, record_url, validate


class SubmissionBusy(RuntimeError):
    pass


def submit_draft(draft_id: str, owner: str, sf, store: DraftStore, mapping: dict) -> dict:
    lock = store.lock(draft_id)
    if not lock.acquire(blocking=False):
        raise SubmissionBusy("Envoi déjà en cours pour ce dossier.")
    try:
        draft = store.get(draft_id, owner)

        if draft["status"] == "submitting":
            # Aucun envoi n'est en cours (verrou libre) : l'essai précédent a été interrompu.
            draft["status"] = "uncertain"

        if draft["status"] in {"draft", "error", "uncertain"}:
            _create_records(draft, sf, store, mapping)

        if draft["status"] == "records_created":
            _upload_documents(draft, sf, store, mapping)

        return draft
    finally:
        lock.release()


def _create_records(draft: dict, sf, store: DraftStore, mapping: dict) -> None:
    errors = validate(draft, mapping)
    if errors:
        raise ValidationError(errors)

    if draft["status"] == "uncertain" and draft["account_choice"].get("mode") != "existing":
        # L'essai précédent a peut-être abouti sans que la réponse nous parvienne.
        company = draft["company"]
        matches = [m for m in sf.search_accounts(company) if m.get("strong")]
        if matches:
            store.save(draft)
            names = ", ".join(f"{m['name']} ({m['match']})" for m in matches)
            raise ValidationError([
                "L'envoi précédent a été interrompu et un compte correspondant existe maintenant dans Salesforce : "
                f"{names}. Vérifiez-le puis choisissez « Utiliser ce compte » au lieu d'en créer un nouveau."
            ])

    plan = build_plan(draft, mapping)
    draft["status"] = "submitting"
    draft["result"] = {}
    store.save(draft)

    try:
        ids = sf.create_records(plan["subrequests"])
    except SalesforceAuthError:
        draft["status"] = "error"
        draft["result"] = {"errors": ["Session Salesforce expirée : reconnectez-vous puis relancez l'envoi."]}
        store.save(draft)
        raise
    except SalesforceError as exc:
        draft["status"] = "error"
        draft["result"] = {"errors": exc.messages, "codes": exc.codes}
        store.save(draft)
        raise
    except httpx.TransportError:
        draft["status"] = "uncertain"
        draft["result"] = {"errors": ["Connexion interrompue avec Salesforce : le résultat est incertain. "
                                      "Relancez l'envoi : l'application vérifiera d'abord qu'aucun compte n'a été créé."]}
        store.save(draft)
        raise

    account_id = plan["account_id"] or ids.get("account")
    contact_id = plan["contact_id"] or ids.get("contact")
    result = {
        "account_id": account_id,
        "contact_id": contact_id,
        "opportunity_id": ids.get("opportunity"),
        "segment_id": ids.get("segment"),
        "summary": plan["summary"],
        "links": {},
    }
    instance = sf.instance_url
    result["links"]["account"] = record_url(instance, mapping["account"]["sobject"], account_id)
    if contact_id:
        result["links"]["contact"] = record_url(instance, mapping["contact"]["sobject"], contact_id)
    if result["opportunity_id"]:
        result["links"]["opportunity"] = record_url(instance, mapping["opportunity"]["sobject"], result["opportunity_id"])
    draft["result"] = result
    draft["status"] = "records_created"
    store.save(draft)


def _upload_documents(draft: dict, sf, store: DraftStore, mapping: dict) -> None:
    result = draft["result"]
    targets = {"account": result.get("account_id"), "opportunity": result.get("opportunity_id"),
               "contact": result.get("contact_id")}
    link_to = [t for t in mapping["files"].get("link_to", ["account"]) if targets.get(t)] or ["account"]
    parent, extra = targets[link_to[0]], [targets[t] for t in link_to[1:]]
    company = draft["company"].get("company_name") or "client"

    file_errors = []
    for kind, doc in draft.get("documents", {}).items():
        if doc.get("uploaded"):
            continue
        data = store.read_file(draft["id"], kind)
        if data is None:
            file_errors.append(f"{kind} : fichier local introuvable, à joindre manuellement dans Salesforce.")
            continue
        title = f"{mapping['files']['titles'].get(kind, kind)} - {company}"
        try:
            doc["content_version_id"] = sf.upload_file(doc.get("filename") or f"{kind}.pdf", title, data, parent, extra)
        except (SalesforceError, httpx.TransportError) as exc:
            msgs = exc.messages if isinstance(exc, SalesforceError) else ["connexion interrompue"]
            file_errors.append(f"{title} : {'; '.join(msgs)}")
            continue
        doc["uploaded"] = True
        store.remove_file(draft["id"], kind)
        store.save(draft)

    result["file_errors"] = file_errors
    if not file_errors:
        draft["status"] = "completed"
    store.save(draft)
