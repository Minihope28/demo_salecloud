"""Lecture des PDF reçus (RC, document fiscal) et proposition de valeurs.

Principes :
- chaque page est lue par deux lecteurs de texte (pypdf et PDFium) et la meilleure lecture est gardée ;
- certains PDF affichent un texte correct mais ont un encodage interne cassé : la lecture donne des
  caractères bizarres. Ces valeurs sont détectées et JAMAIS proposées ; si l'OCR (Tesseract) est
  disponible, la page est relue comme une image ;
- chaque valeur proposée garde sa source (document, page, méthode, extrait) pour que le commercial la vérifie ;
- une information introuvable reste vide, pour une saisie manuelle ;
- les libellés recherchés sont des hypothèses sur la structure des RC marocains : à calibrer sur de vrais documents.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

try:  # PDFium : second lecteur de texte + rendu des pages pour l'OCR
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover - dépendance déclarée, garde-fou seulement
    pdfium = None

MIN_TEXT_CHARS = 40      # en dessous, la page est considérée comme une image (scan)
GOOD_PAGE_QUALITY = 0.97  # en dessous, la page contient des caractères illisibles
GARBLED_VALUE = 0.8       # une valeur sous ce seuil n'est jamais proposée
EXTRA_OK = set("«»’‘“”–—°€…•·'")


class DocumentError(ValueError):
    """Le fichier ne peut pas être traité (pas un PDF, trop volumineux, illisible…)."""


# =========================================================================== qualité du texte

def _is_arabic(cp: int) -> bool:
    return 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF


def text_quality(text: str) -> float:
    """Part des caractères « normaux » (latin, chiffres, ponctuation). L'arabe est neutre (RC bilingues)."""
    good = bad = 0
    for ch in text:
        if ch.isspace():
            continue
        cp = ord(ch)
        if _is_arabic(cp):
            continue
        if 0x21 <= cp < 0x7F or (0xC0 <= cp <= 0x17F and cp not in (0xD7, 0xF7)) or ch in EXTRA_OK:
            good += 1
        else:
            bad += 1
    return 1.0 if good + bad == 0 else good / (good + bad)


def is_garbled(value: str) -> bool:
    return "(cid:" in value or "\ufffd" in value or text_quality(value) < GARBLED_VALUE


# =========================================================================== OCR

def find_tesseract(setting: str | None = None) -> str | None:
    """Chemin de Tesseract : réglage explicite, PATH, puis emplacement Windows habituel."""
    candidates = [setting] if setting else []
    candidates += [shutil.which("tesseract") or "",
                   r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                   r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return None


class TesseractOCR:
    def __init__(self, cmd: str, lang: str = "fra", dpi: int = 300, timeout: int = 90, psm: int = 4):
        self.cmd, self.lang, self.dpi, self.timeout, self.psm = cmd, lang, dpi, timeout, psm

    def page_text(self, data: bytes, index: int) -> str:
        if pdfium is None:
            return ""
        doc = pdfium.PdfDocument(data)
        try:
            image = doc[index].render(scale=self.dpi / 72).to_pil()
        finally:
            doc.close()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.png"
            image.save(path)
            proc = subprocess.run(
                [self.cmd, str(path), "stdout", "-l", self.lang, "--psm", str(self.psm),
                 "-c", "preserve_interword_spaces=1"],
                capture_output=True, timeout=self.timeout, check=False,
            )
        return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else ""


# =========================================================================== lecture du PDF

@dataclass
class PdfText:
    pages: list[str]
    quality: list[float] = field(default_factory=list)
    ocr_pages: dict[int, str] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text(self) -> bool:
        return sum(len(p.strip()) for p in self.pages) >= MIN_TEXT_CHARS

    @property
    def problem_pages(self) -> list[int]:
        """Pages sans texte (scan) ou avec des caractères illisibles."""
        return [i for i, (p, q) in enumerate(zip(self.pages, self.quality))
                if len(p.strip()) < MIN_TEXT_CHARS or q < GOOD_PAGE_QUALITY]


def _pdfium_pages(data: bytes) -> list[str]:
    if pdfium is None:
        return []
    doc = pdfium.PdfDocument(data)
    try:
        out = []
        for page in doc:
            textpage = page.get_textpage()
            out.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
            textpage.close()
            page.close()
        return out
    except Exception:  # noqa: BLE001 - un échec de PDFium ne doit pas bloquer la lecture pypdf
        return []
    finally:
        doc.close()


def read_pdf(data: bytes, max_bytes: int, max_pages: int, ocr: TesseractOCR | None = None,
             max_ocr_pages: int = 5) -> PdfText:
    if len(data) > max_bytes:
        raise DocumentError(f"Fichier trop volumineux (maximum {max_bytes // (1024 * 1024)} Mo).")
    if not data.lstrip()[:5] == b"%PDF-":
        raise DocumentError("Le fichier n'est pas un PDF.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("PDF protégé par mot de passe : saisie manuelle nécessaire.")
        if len(reader.pages) > max_pages:
            raise DocumentError(f"PDF trop long (maximum {max_pages} pages).")
        pypdf_pages = [(page.extract_text() or "") for page in reader.pages]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise DocumentError("PDF illisible ou endommagé.") from exc

    alt = _pdfium_pages(data)
    pages, quality = [], []
    for i, text in enumerate(pypdf_pages):
        options = [text] + ([alt[i]] if i < len(alt) else [])
        best = max(options, key=lambda t: (round(text_quality(t), 2), len(t.strip())))
        pages.append(best)
        quality.append(text_quality(best))

    pdf = PdfText(pages=pages, quality=quality)
    if ocr:
        for i in pdf.problem_pages[:max_ocr_pages]:
            try:
                pdf.ocr_pages[i] = ocr.page_text(data, i)
            except (OSError, subprocess.SubprocessError):
                pass
    return pdf


# =========================================================================== normalisation

def _fold(text: str) -> str:
    """Minuscule sans accents, en conservant la longueur (positions alignées avec l'original)."""
    out = []
    for ch in text:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(base.lower() if len(base) == 1 else ch.lower())
    return "".join(out)


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" :;,.-–—|\t")
    return value.strip()


# =========================================================================== règles

SEP = r"\s*(?:n\s*[°o]\.?|no\.?|numero|num\.?)?\s*[:\-–.|]*\s*"

# Chaque règle : libellés (déjà « foldés » : minuscules, sans accents) et motif de la valeur.
FIELD_RULES: dict[str, dict] = {
    "company_name": {
        "labels": [r"denomination sociale", r"denomination", r"raison sociale", r"nom de la societe"],
        "value": r"(.{2,120})",
    },
    "common_name": {
        "labels": [r"nom commercial", r"enseigne"],
        "value": r"(.{2,120})",
    },
    "rc_number": {
        "labels": [
            r"registre de commerce", r"numero du registre(?: analytique)?", r"n[°o]\s*(?:du\s*)?r\.?c\.?",
            r"\br\.?c\.?(?=\s*(?:n[°o]|:|\d))", r"(?:numero|n[°o]) d'immatriculation",
        ],
        "value": r"([0-9][0-9 /.\-]{0,14}[0-9]|[0-9])",
    },
    "legal_form": {
        "labels": [r"forme juridique(?: de la societe| de l'entreprise| de la personne morale)?"],
        "value": r"(.{2,80})",
    },
    "ice": {
        "labels": [r"identifiant commun de l'entreprise", r"\bi\.?c\.?e\.?\b"],
        "value": r"(\d[\d ]{13,20}\d)",
    },
    "tax_id": {
        "labels": [r"identifiant fiscal", r"identification fiscale", r"\bi\.f\.?\b", r"\bif\b(?=\s*(?:n[°o]|:|\d))"],
        "value": r"(\d[\d ]{4,12}\d)",
    },
    "address": {
        "labels": [r"adresse du siege(?: social)?", r"siege social", r"adresse"],
        "value": r"(.{4,160})",
    },
    "capital": {
        "labels": [r"capital social", r"capital"],
        "value": r"(\d[\d .,]*\s*(?:dh|dhs|mad|dirhams?)?)",
    },
    "activity": {
        "labels": [r"activite(?:s)? exercee(?:s)?", r"objet social", r"activite principale", r"activite"],
        "value": r"(.{3,160})",
    },
    "manager_name": {
        "labels": [r"gerant(?:e)?(?:\s*\(s\))?", r"dirigeant", r"representant legal"],
        "value": r"(.{3,80})",
    },
}

LEGAL_FORMS = [
    ("SARL AU", r"\bs\.?a\.?r\.?l\.?\s*(?:a\.?u\.?|associe unique)(?!\w)"),
    ("SARL", r"\bs\.?a\.?r\.?l\.?(?!\w)"),
    ("SAS", r"\bs\.?a\.?s\.?(?!\w)"),
    ("SA", r"\bs\.?a\.?(?!\w)"),
    ("SNC", r"\bs\.?n\.?c\.?(?!\w)"),
    ("Entreprise individuelle", r"\b(?:personne physique|entreprise individuelle|commercant)\b"),
]
INDIVIDUAL_FORMS = {"Entreprise individuelle"}

ICE_PATTERN = re.compile(r"(?<!\d)(\d{15})(?!\d)")
ALL_LABELS = [lab for rule in FIELD_RULES.values() for lab in rule["labels"]]
NEXT_LABEL = re.compile(r"\s\|\s|\s(?=(?:" + "|".join(ALL_LABELS) + r")\s*[:\-–])")


def _looks_like_label(folded_line: str) -> bool:
    return any(re.match(r"\s*" + lab, folded_line) for lab in ALL_LABELS)


def _cut_at_next_label(value: str) -> str:
    """Dans un tableau, la ligne peut contenir la valeur puis un autre libellé : on coupe avant."""
    m = NEXT_LABEL.search(_fold(value))
    return value[: m.start()] if m and m.start() > 1 else value


def _find_field(pages: list[str], rule: dict) -> tuple[str, int, str] | None:
    for page_no, page in enumerate(pages, start=1):
        lines = page.splitlines()
        folded = [_fold(l) for l in lines]
        for label in rule["labels"]:
            for idx, fl in enumerate(folded):
                m = re.search(label + SEP, fl)
                if not m:
                    continue
                rest = lines[idx][m.end():]
                vm = re.match(r"\s*" + rule["value"], rest, re.I)
                if vm and _clean(vm.group(1)):
                    return _clean(_cut_at_next_label(vm.group(1))), page_no, _clean(lines[idx])
                # valeur sur la ligne suivante
                if not rest.strip():
                    for nxt in range(idx + 1, min(idx + 3, len(lines))):
                        if not lines[nxt].strip():
                            continue
                        if _looks_like_label(folded[nxt]):
                            break
                        vm = re.match(r"\s*" + rule["value"], lines[nxt], re.I)
                        if vm and _clean(vm.group(1)):
                            return _clean(_cut_at_next_label(vm.group(1))), page_no, _clean(lines[idx] + " " + lines[nxt])
                        break
    return None


def _normalise_value(key: str, value: str) -> str:
    if key in {"ice", "tax_id", "rc_number"}:
        return re.sub(r"\s+", "", value)
    return value


def _detect_legal_form(pages: list[str], hint: str | None) -> tuple[str, int, str] | None:
    candidates = [(hint, 0, hint)] if hint else []
    candidates += [(p, i, None) for i, p in enumerate(pages, start=1)]
    for text, page_no, snippet in candidates:
        folded = _fold(text)
        for label, pattern in LEGAL_FORMS:
            m = re.search(pattern, folded)
            # « sa », « sas » sont aussi des mots courants : on exige des majuscules pour les sigles courts.
            if m and label in {"SA", "SAS", "SNC"} and not text[m.start():m.end()].replace(".", "").isupper():
                m = None
            if m:
                context = snippet or _clean(text[max(0, m.start() - 30): m.end() + 30])
                return label, page_no, context
    return None


def _extract(pages: list[str], source: str, method: str) -> tuple[dict[str, dict], set[str]]:
    """Cherche les champs dans des pages. Retourne (valeurs lisibles, champs trouvés mais illisibles)."""
    found: dict[str, dict] = {}
    unreadable: set[str] = set()

    def keep(key: str, value: str, page_no: int, snippet: str) -> None:
        if is_garbled(value):
            unreadable.add(key)
            return
        found[key] = {"value": value, "source": source, "page": page_no, "method": method, "snippet": snippet[:200]}

    for key, rule in FIELD_RULES.items():
        hit = _find_field(pages, rule)
        if hit:
            value, page_no, snippet = hit
            keep(key, _normalise_value(key, value), page_no, snippet)

    # L'ICE fait 15 chiffres : on le cherche aussi sans libellé si besoin.
    if "ice" in found and not re.fullmatch(r"\d{15}", found["ice"]["value"]):
        del found["ice"]
    if "ice" not in found:
        for page_no, page in enumerate(pages, start=1):
            if "ice" in _fold(page):
                m = ICE_PATTERN.search(page.replace(" ", ""))
                if m:
                    keep("ice", m.group(1), page_no, "Nombre à 15 chiffres près de la mention ICE")
                    unreadable.discard("ice")
                    break

    # Forme juridique : libellé explicite, sinon mot-clé dans la dénomination ou le texte.
    legal_hint = found.get("legal_form", {}).get("value")
    form = _detect_legal_form(pages, legal_hint or found.get("company_name", {}).get("value"))
    if form:
        label, page_no, snippet = form
        page_no = page_no or found.get("legal_form", found.get("company_name", {})).get("page", 1)
        found["legal_form"] = {"value": label, "source": source, "page": page_no, "method": method,
                               "snippet": (snippet or "")[:200]}
        unreadable.discard("legal_form")
    elif "legal_form" in found:
        del found["legal_form"]  # texte non reconnu comme une forme juridique : on ne propose rien

    if "legal_form" in found:
        lf = found["legal_form"]
        found["sole_proprietorship"] = {**lf, "value": "Oui" if lf["value"] in INDIVIDUAL_FORMS else "Non",
                                        "snippet": f"Déduit de la forme juridique : {lf['value']}"}

    return found, unreadable - set(found)


def extract_fields(pdf: PdfText, source: str) -> tuple[dict[str, dict], set[str]]:
    found, unreadable = _extract(pdf.pages, source, "texte") if pdf.has_text else ({}, set())
    if pdf.ocr_pages:
        ocr_pages = [pdf.ocr_pages.get(i, "") for i in range(pdf.page_count)]
        ocr_found, ocr_unreadable = _extract(ocr_pages, source, "OCR")
        for key, hit in ocr_found.items():
            if key not in found:
                found[key] = hit
                unreadable.discard(key)
        unreadable |= {k for k in ocr_unreadable if k not in found}
    return found, unreadable


def analyse_document(data: bytes, source: str, max_bytes: int, max_pages: int,
                     ocr: TesseractOCR | None = None) -> dict:
    """Point d'entrée : lit le PDF et renvoie un résumé exploitable par l'interface."""
    pdf = read_pdf(data, max_bytes=max_bytes, max_pages=max_pages, ocr=ocr)
    fields, unreadable = extract_fields(pdf, source)
    problems = pdf.problem_pages
    ocr_used = any(v.strip() for v in pdf.ocr_pages.values())

    warnings = []
    if problems and not ocr_used:
        if not pdf.has_text:
            warnings.append("Document scanné (image) : aucun texte lisible. Les informations sont à saisir manuellement"
                            + ("." if ocr else " (ou activer l'OCR)."))
        else:
            warnings.append("Une partie du texte de ce PDF est mal encodée (caractères illisibles) : "
                            "ces informations ne sont pas proposées et restent à saisir"
                            + ("." if ocr else " (ou activer l'OCR pour les lire)."))
    if ocr_used:
        warnings.append("Des pages ont été lues par OCR (comme une image) : vérifiez attentivement les valeurs.")

    return {
        "pages": pdf.page_count,
        "text_found": pdf.has_text or ocr_used,
        "fields": fields,
        "unreadable": sorted(unreadable),
        "ocr_used": ocr_used,
        "warning": " ".join(warnings) or None,
    }
