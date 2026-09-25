"""Génère des PDF d'exemple 100 % fictifs (aucune donnée client réelle).

Usage : python scripts/generate_samples.py   (nécessite reportlab, cf. requirements-dev.txt)
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "samples"


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


def main() -> None:
    OUT.mkdir(exist_ok=True)
    _write(OUT / "RC_exemple_fictif.pdf", "EXTRAIT DU REGISTRE DE COMMERCE", [
        "Tribunal de commerce de Démoville",
        "Numéro du registre analytique : 99001",
        "Dénomination : ATLAS DÉMO TRANS",
        "Forme juridique : SARL AU",
        "Capital social : 100 000,00 DH",
        "Siège social : 21, avenue de la Démonstration, Quartier Exemple",
        "Activités exercées : Transport de marchandises pour compte d'autrui",
        "Gérant : M. Karim EXEMPLE",
        "Date d'immatriculation : 01/02/2024",
    ])
    _write(OUT / "Attestation_fiscale_exemple_fictif.pdf", "ATTESTATION FISCALE", [
        "Raison sociale : ATLAS DÉMO TRANS SARL AU",
        "Identifiant fiscal : 99887766",
        "ICE : 009988776600055",
        "Adresse : 21, avenue de la Démonstration, Quartier Exemple",
    ])
    print(f"PDF fictifs générés dans {OUT}")


if __name__ == "__main__":
    main()
