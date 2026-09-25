from app.drafts import new_draft
from app.salesforce import build_plan, validate


def complete_draft():
    d = new_draft("demo")
    d["company"] = {"company_name": "ATLAS DÉMO TRANS", "rc_number": "99001", "ice": "009988776600055",
                    "sole_proprietorship": "Non", "address": "21 avenue", "postal_code": "99100", "city": "Villetest",
                    "tax_id_type": "Numéro d'identification fiscale", "tax_id": "99887766", "common_name": ""}
    d["contact"] = {"first_name": "Nadia", "last_name": "EXEMPLE", "mobile": "+212600000000", "email": ""}
    d["segment"] = {"segment_activity": "Transport de marchandises", "segment_fleet_size": "12"}
    d["opportunity"] = {"name": "Atlas - 3 camions", "close_date": "2026-12-31", "quantity": "3", "description": "Tracteurs"}
    d["verified"] = True
    return d


def test_new_account_plan_uses_references(mapping):
    plan = build_plan(complete_draft(), mapping)
    refs = [s["referenceId"] for s in plan["subrequests"]]
    assert refs == ["account", "segment", "contact", "opportunity", "contactRole"]
    by_ref = {s["referenceId"]: s for s in plan["subrequests"]}
    assert by_ref["account"]["body"]["Name"] == "ATLAS DÉMO TRANS"
    assert by_ref["account"]["body"]["RC_Number__c"] == "99001"
    assert by_ref["account"]["body"]["BillingCountryCode"] == "MA"  # pays toujours Maroc
    assert by_ref["account"]["body"]["Sole_Proprietorship__c"] == "Non"
    assert by_ref["account"]["body"]["BillingCity"] == "Villetest"
    assert "common_name" not in by_ref["account"]["body"]  # champ vide / non mappé non envoyé
    assert by_ref["segment"]["body"]["Account__c"] == "@{account.id}"
    assert by_ref["contact"]["body"]["AccountId"] == "@{account.id}"
    assert by_ref["opportunity"]["body"]["StageName"] == "Prospecting"
    # quantité non mappée : conservée dans la description plutôt que perdue
    assert "Quantité envisagée : 3" in by_ref["opportunity"]["body"]["Description"]
    assert by_ref["contactRole"]["body"] == {"OpportunityId": "@{opportunity.id}", "ContactId": "@{contact.id}",
                                             "Role": "Decision Maker", "IsPrimary": True}
    assert all(s["url"].startswith("/services/data/v62.0/sobjects/") for s in plan["subrequests"])


def test_existing_account_and_contact_are_reused(mapping):
    d = complete_draft()
    d["account_choice"] = {"mode": "existing", "id": "001000000000001AAA", "name": "ATLAS"}
    d["contact_choice"] = {"mode": "existing", "id": "003000000000001AAA", "name": "Nadia"}
    plan = build_plan(d, mapping)
    assert [s["referenceId"] for s in plan["subrequests"]] == ["opportunity", "contactRole"]
    assert plan["subrequests"][0]["body"]["AccountId"] == "001000000000001AAA"
    assert plan["subrequests"][1]["body"]["ContactId"] == "003000000000001AAA"
    assert plan["account_id"] == "001000000000001AAA"
    assert validate(d, mapping) == []


def test_validation_messages(mapping):
    d = complete_draft()
    assert validate(d, mapping) == []
    d["verified"] = False
    d["company"]["ice"] = "123"
    d["company"]["postal_code"] = "5200"
    d["company"]["city"] = ""
    d["company"]["tax_id"] = ""
    d["contact"] = {"last_name": "", "mobile": "", "email": "pas-un-email"}
    d["segment"] = {}
    d["opportunity"]["close_date"] = ""
    errors = " | ".join(validate(d, mapping))
    for expected in ["Cochez", "15 chiffres", "5 chiffres", "Ville de facturation", "Numéro d'identification fiscale", "Nom du contact", "e-mail du contact invalide", "Activité du client", "Date de clôture"]:
        assert expected in errors


def test_existing_contact_requires_existing_account(mapping):
    d = complete_draft()
    d["contact_choice"] = {"mode": "existing", "id": "003000000000001AAA"}
    assert any("contact existant" in e for e in validate(d, mapping))


def test_picklist_values_are_translated(mapping):
    mapping["account"]["value_maps"]["sole_proprietorship"] = {"Oui": "Yes", "Non": "No"}
    body = build_plan(complete_draft(), mapping)["subrequests"][0]["body"]
    assert body["Sole_Proprietorship__c"] == "No"
