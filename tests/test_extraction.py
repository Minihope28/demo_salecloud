import io

import pytest
from reportlab.pdfgen import canvas

from app.config import BASE_DIR
from app.extraction import DocumentError, analyse_document

LIMIT = 8 * 1024 * 1024


def _pdf(lines):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 18
    if not lines:
        c.rect(50, 50, 200, 200, fill=1)  # page « scannée » : aucun texte
    c.save()
    return buf.getvalue()


def test_sample_rc_fields():
    data = (BASE_DIR / "samples" / "RC_exemple_fictif.pdf").read_bytes()
    result = analyse_document(data, "RC", LIMIT, 40)
    f = {k: v["value"] for k, v in result["fields"].items()}
    assert result["text_found"] is True
    assert f["company_name"] == "ATLAS DÉMO TRANS"
    assert f["rc_number"] == "99001"
    assert f["legal_form"] == "SARL AU"
    assert f["address"].startswith("21, avenue")
    assert f["manager_name"] == "M. Karim EXEMPLE"
    assert result["fields"]["rc_number"]["page"] == 1


def test_sample_fiscal_fields():
    data = (BASE_DIR / "samples" / "Attestation_fiscale_exemple_fictif.pdf").read_bytes()
    f = {k: v["value"] for k, v in analyse_document(data, "Document fiscal", LIMIT, 40)["fields"].items()}
    assert f["ice"] == "009988776600055"
    assert f["tax_id"] == "99887766"


def test_value_on_next_line_and_spaced_ice():
    data = _pdf(["Raison sociale :", "SOCIETE EXEMPLE TRANS", "I.C.E : 001 234 567 000 089", "R.C. n° 4521"])
    f = {k: v["value"] for k, v in analyse_document(data, "RC", LIMIT, 40)["fields"].items()}
    assert f["company_name"] == "SOCIETE EXEMPLE TRANS"
    assert f["ice"] == "001234567000089"
    assert f["rc_number"] == "4521"


def test_immatriculation_date_is_not_taken_as_rc():
    data = _pdf(["Dénomination : EXEMPLE SARL", "Date d'immatriculation : 01/02/2024"])
    fields = analyse_document(data, "RC", LIMIT, 40)["fields"]
    assert "rc_number" not in fields


def test_scanned_pdf_is_flagged():
    result = analyse_document(_pdf([]), "RC", LIMIT, 40)
    assert result["text_found"] is False
    assert result["fields"] == {}
    assert "manuellement" in result["warning"]


def test_rejects_non_pdf_and_big_files():
    with pytest.raises(DocumentError):
        analyse_document(b"hello", "RC", LIMIT, 40)
    with pytest.raises(DocumentError):
        analyse_document(b"%PDF-" + b"0" * 100, "RC", 50, 40)


# --------------------------------------------------------------------------- PDF mal encodé
from pathlib import Path  # noqa: E402

from app.extraction import TesseractOCR, find_tesseract, is_garbled  # noqa: E402

BROKEN = Path(__file__).parent / "fixtures" / "rc_encodage_casse.pdf"


def test_garbled_values_are_never_proposed():
    result = analyse_document(BROKEN.read_bytes(), "RC", LIMIT, 40)
    assert "company_name" not in result["fields"] and "address" not in result["fields"]
    assert result["unreadable"] == ["address", "company_name"]
    assert "mal encodée" in result["warning"]
    # les valeurs lisibles de la même page restent proposées
    assert result["fields"]["rc_number"]["value"] == "99001"
    assert result["fields"]["sole_proprietorship"]["value"] == "Non"


@pytest.mark.skipif(not find_tesseract(), reason="Tesseract non installé")
def test_ocr_reads_garbled_values():
    result = analyse_document(BROKEN.read_bytes(), "RC", LIMIT, 40, ocr=TesseractOCR(find_tesseract()))
    assert result["ocr_used"] is True and result["unreadable"] == []
    assert result["fields"]["company_name"]["value"] == "ATLAS DÉMO TRANS"
    assert result["fields"]["company_name"]["method"] == "OCR"
    assert "Villetest" in result["fields"]["address"]["value"]


def test_garble_detection():
    assert is_garbled("ΔjɰγΗϟ□")
    assert is_garbled("ƒΣɰϟ□□ΩO□6HϟΔHAΛɰϟ□□Δ9jΛ6ϟ□")
    assert not is_garbled("SOCIÉTÉ ÉTOILE D'OR – Boulevard Hassan II, n° 12")


def test_legal_form_label_variant_and_lowercase_sa():
    data = _pdf(["Dénomination : EXEMPLE TRANS", "Forme juridique de la société : SARL",
                 "Activité : transport pour sa clientèle"])
    f = {k: v["value"] for k, v in analyse_document(data, "RC", LIMIT, 40)["fields"].items()}
    assert f["legal_form"] == "SARL" and f["sole_proprietorship"] == "Non"
    data = _pdf(["Nom : Ali EXEMPLE", "Activité : transport pour sa clientèle", "Adresse : 3 rue Test"])
    fields = analyse_document(data, "RC", LIMIT, 40)["fields"]
    assert "legal_form" not in fields  # « sa » n'est pas le sigle SA


@pytest.mark.skipif(not __import__("app.extraction", fromlist=["RapidOCR"]).RapidOCR.available(), reason="RapidOCR non installé")
def test_builtin_ocr_reads_garbled_values():
    from app.extraction import RapidOCR
    result = analyse_document(BROKEN.read_bytes(), "RC", LIMIT, 40, ocr=RapidOCR())
    assert result["ocr_used"] is True and result["unreadable"] == []
    assert result["fields"]["company_name"]["method"] == "OCR"
    assert result["fields"]["company_name"]["value"].replace("É", "E") == "ATLAS DEMO TRANS"
    assert "Villetest" in result["fields"]["address"]["value"]
    assert result["fields"]["legal_form"]["value"] == "SARL AU"


def test_fiscal_document_only_keeps_if_and_ice():
    data = (BASE_DIR / "samples" / "Attestation_fiscale_exemple_fictif.pdf").read_bytes()
    fields = analyse_document(data, "Document fiscal", LIMIT, 40, only={"tax_id", "ice"})["fields"]
    assert set(fields) == {"tax_id", "ice"}
