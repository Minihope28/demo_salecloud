"""Génère les PDF d'exemple, 100 % fictifs (aucune donnée client réelle).

- samples/RC_exemple_fictif.pdf, samples/Attestation_fiscale_exemple_fictif.pdf : PDF texte normaux ;
- tests/fixtures/rc_encodage_casse.pdf : reproduit un RC dont l'affichage est correct mais dont le
  texte interne est mal encodé (caractères bizarres à la lecture), comme certains RC réels.

Usage : python scripts/generate_samples.py   (nécessite reportlab et Pillow, cf. requirements-dev.txt)
"""

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
FIXTURES = ROOT / "tests" / "fixtures"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

RC_LINES = [
    "Tribunal de commerce de Démoville",
    "Numéro du registre analytique : 99001",
    "Dénomination : ATLAS DÉMO TRANS",
    "Forme juridique de la société : SARL AU",
    "Capital social : 100 000,00 DH",
    "Siège social : 21, avenue de la Démonstration, Villetest, Province Exemple",
    "Activités exercées : Transport de marchandises pour compte d'autrui",
    "Gérant : M. Karim EXEMPLE",
    "Date d'immatriculation : 01/02/2024",
]


def _write(path: Path, title: str, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(title)
    y = 800
    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, y, title)
    y -= 18
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(60, y, "DOCUMENT FICTIF - EXEMPLE DE DÉMONSTRATION")
    y -= 30
    c.setFont("Helvetica", 11)
    for line in lines:
        c.drawString(60, y, line)
        y -= 20
    c.save()


def _broken_encoding_rc(path: Path) -> None:
    """Image correcte + couche texte invisible dont les VALEURS sont des caractères sans rapport."""
    width, height = A4
    scale = 300 / 72
    img = Image.new("RGB", (int(width * scale), int(height * scale)), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(DEJAVU), int(11 * scale))
    bold = ImageFont.truetype(str(DEJAVU), int(13 * scale))
    title = "EXTRAIT DU REGISTRE DE COMMERCE"
    draw.text((60 * scale, (height - 800) * scale), title, font=bold, fill="black")
    y = 770
    for line in RC_LINES:
        draw.text((60 * scale, (height - y) * scale), line, font=font, fill="black")
        y -= 22
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    pdfmetrics.registerFont(TTFont("DejaVu", str(DEJAVU)))
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(title)
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 0, 0, width=width, height=height)
    text = c.beginText()
    text.setTextRenderMode(3)  # invisible, comme une couche texte « cassée »
    text.setFont("DejaVu", 11)
    garbled = {"ATLAS DÉMO TRANS": "ΔjɰγΗϟ□", "21, avenue de la Démonstration, Villetest, Province Exemple": "ƒΣɰϟ□□ΩO□6HϟΔHAΛɰϟ□□Δ9jΛ6ϟ□"}
    y = 770
    for line in RC_LINES:
        label, _, value = line.partition(" : ")
        text.setTextOrigin(60, y)
        text.textLine(f"{label} : {garbled.get(value, value)}" if value else line)
        y -= 22
    c.drawText(text)
    c.save()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    _write(OUT / "RC_exemple_fictif.pdf", "EXTRAIT DU REGISTRE DE COMMERCE", RC_LINES)
    _write(OUT / "Attestation_fiscale_exemple_fictif.pdf", "ATTESTATION FISCALE", [
        "Raison sociale : ATLAS DÉMO TRANS SARL AU",
        "Identifiant fiscal : 99887766",
        "ICE : 009988776600055",
        "Adresse : 21, avenue de la Démonstration, Villetest",
    ])
    _broken_encoding_rc(FIXTURES / "rc_encodage_casse.pdf")
    print(f"PDF fictifs générés dans {OUT} et {FIXTURES}")


if __name__ == "__main__":
    main()
