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
