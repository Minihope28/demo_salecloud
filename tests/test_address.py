"""Ville, code postal et vérification (référentiels fictifs : aucun vrai code postal n'est utilisé ici)."""

from openpyxl import Workbook

from app.address import PostalReference, analyse_address, check_postal, load_reference
from app.config import BASE_DIR

DEMO = BASE_DIR / "samples" / "codes_postaux_demo.csv"


def ref_with_province(tmp_path):
    # « Chefville » est à la fois une localité et la province de « Petiteville » (codes fictifs).
    path = tmp_path / "ref.csv"
    path.write_text("LOCALITE,CODE POSTAL,PROVINCE\nChefville,98000,Chefville\nPetiteville,98600,Chefville\n", encoding="cp1252")
    return PostalReference.load(path)


def test_locality_preferred_over_its_province_and_spelling_variant(tmp_path):
    ref = ref_with_province(tmp_path)
    # adresse sur le modèle « AV HASSAN2 TINJDAD, Errachidia » : ville mal orthographiée + province
    res = analyse_address("AV HASSAN2 PETITVILLE, Chefville", ref)
    assert res["city"]["value"] == "Petiteville"
    assert res["postal_code"]["value"] == "98600"
    assert res["street"]["value"] == "AV HASSAN2"
    assert res["region"]["value"] == "Chefville"


def test_postal_code_in_address_is_checked(tmp_path):
    ref = ref_with_province(tmp_path)
    res = analyse_address("Bd Principal 98000 Petiteville", ref)
    assert res["postal_code"]["value"] == "98000" and res["city"]["value"] == "Petiteville"
    check = check_postal("Petiteville", "98000", ref)
    assert check["status"] == "warning"
    assert "98600" in check["message"] and "Chefville" in check["message"]
    assert check_postal("petiteville", "98600", ref)["status"] == "ok"


def test_unknown_city_gets_suggestions(tmp_path):
    ref = ref_with_province(tmp_path)
    check = check_postal("Petitevile", "98600", ref)
    assert check["status"] == "warning" and check["suggestions"] == ["Petiteville"]


def test_city_with_several_codes_is_not_guessed():
    ref, _ = load_reference([DEMO])
    res = analyse_address("12 rue du Centre, Grandeville", ref)
    assert res["city"]["value"] == "Grandeville"
    assert res["postal_code"] is None
    assert "plusieurs codes postaux" in res["notes"][0]


def test_without_reference_simple_rules():
    res = analyse_address("12 rue du Centre, 99000 Démoville", None)
    assert res["city"]["value"] == "Démoville" and res["postal_code"]["value"] == "99000"
    assert check_postal("Démoville", "99000", None)["status"] == "unknown"
    assert check_postal("Démoville", "990", None)["status"] == "error"


def test_xlsx_reference_and_missing_file(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Commune", "Code postal", "Province", "Région"])
    ws.append(["Villexl", 97000, "Province XL", "Région XL"])
    path = tmp_path / "codes_postaux.xlsx"
    wb.save(path)
    ref, status = load_reference([tmp_path / "absent.csv", path])
    assert ref and ref.get("VILLEXL").codes == ["97000"] and "1 localités" in status
    none, status = load_reference([tmp_path / "absent.csv"])
    assert none is None and "non chargé" in status
