"""Lecture des PDF reçus (RC, document fiscal) et proposition de valeurs.

Principes :
- on ne lit que les PDF contenant du texte ; un scan (image) est signalé pour saisie manuelle ;
- chaque valeur proposée garde sa source (document, page, extrait) pour que le commercial la vérifie ;
- les libellés recherchés sont des hypothèses sur la structure des RC marocains : ils doivent être
  calibrés sur de vrais documents (voir docs/proposition.md).
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MIN_TEXT_CHARS = 40  # en dessous, on considère le PDF comme un scan sans texte exploitable


class DocumentError(ValueError):
    """Le fichier ne peut pas être traité (pas un PDF, trop volumineux, illisible…)."""


@dataclass
class PdfText:
    pages: list[str]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text(self) -> bool:
        return sum(len(p.strip()) for p in self.pages) >= MIN_TEXT_CHARS


def read_pdf(data: bytes, max_bytes: int, max_pages: int) -> PdfText:
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
        pages = [(page.extract_text() or "") for page in reader.pages]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise DocumentError("PDF illisible ou endommagé.") from exc
    return PdfText(pages=pages)


# --------------------------------------------------------------------------- normalisation

def _fold(text: str) -> str:
    """Minuscule sans accents, en conservant la longueur (positions alignées avec l'original)."""
    out = []
    for ch in text:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(base.lower() if len(base) == 1 else ch.lower())
    return "".join(out)


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" :;,.-–—\t")
    return value.strip()


# --------------------------------------------------------------------------- règles

SEP = r"\s*(?:n\s*[°o]\.?|no\.?|numero|num\.?)?\s*[:\-–.]*\s*"

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
        "labels": [r"forme juridique"],
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
    ("SARL AU", r"\bs\.?a\.?r\.?l\.?\s*(?:a\.?u\.?|associe unique)\b"),
    ("SARL", r"\bs\.?a\.?r\.?l\.?\b"),
    ("SAS", r"\bs\.?a\.?s\.?\b"),
    ("SA", r"\bs\.?a\.?\b(?!\w)"),
    ("SNC", r"\bs\.?n\.?c\.?\b"),
    ("Personne physique", r"\bpersonne physique\b"),
]

ICE_PATTERN = re.compile(r"(?<!\d)(\d{15})(?!\d)")
ALL_LABELS = [lab for rule in FIELD_RULES.values() for lab in rule["labels"]]


def _looks_like_label(folded_line: str) -> bool:
    return any(re.match(r"\s*" + lab, folded_line) for lab in ALL_LABELS)


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
                    return _clean(vm.group(1)), page_no, _clean(lines[idx])
                # valeur sur la ligne suivante
                if not rest.strip():
                    for nxt in range(idx + 1, min(idx + 3, len(lines))):
                        if not lines[nxt].strip():
                            continue
                        if _looks_like_label(folded[nxt]):
                            break
                        vm = re.match(r"\s*" + rule["value"], lines[nxt], re.I)
                        if vm and _clean(vm.group(1)):
                            return _clean(vm.group(1)), page_no, _clean(lines[idx] + " " + lines[nxt])
                        break
    return None


def _normalise_value(key: str, value: str) -> str:
    if key in {"ice", "tax_id"}:
        return re.sub(r"\s", "", value)
    if key == "rc_number":
        return re.sub(r"\s+", "", value)
    return value


def _detect_legal_form(pages: list[str], hint: str | None) -> tuple[str, int, str] | None:
    candidates = [(hint, 0, hint)] if hint else []
    candidates += [(p, i, None) for i, p in enumerate(pages, start=1)]
    for text, page_no, snippet in candidates:
        folded = _fold(text)
        for label, pattern in LEGAL_FORMS:
            m = re.search(pattern, folded)
            if m:
                context = snippet or _clean(text[max(0, m.start() - 30): m.end() + 30])
                return label, page_no, context
    return None


def extract_fields(pdf: PdfText, source: str) -> dict[str, dict]:
    """Retourne {champ: {value, source, page, snippet}} pour les valeurs trouvées."""
    found: dict[str, dict] = {}
    if not pdf.has_text:
        return found

    for key, rule in FIELD_RULES.items():
        hit = _find_field(pdf.pages, rule)
        if hit:
            value, page_no, snippet = hit
            found[key] = {"value": _normalise_value(key, value), "source": source, "page": page_no, "snippet": snippet[:200]}

    # L'ICE fait 15 chiffres : on le cherche aussi sans libellé si besoin.
    if "ice" in found and not re.fullmatch(r"\d{15}", found["ice"]["value"]):
        del found["ice"]
    if "ice" not in found:
        for page_no, page in enumerate(pdf.pages, start=1):
            if "ice" in _fold(page):
                m = ICE_PATTERN.search(page.replace(" ", ""))
                if m:
                    found["ice"] = {"value": m.group(1), "source": source, "page": page_no,
                                    "snippet": "Nombre à 15 chiffres près de la mention ICE"}
                    break

    # Forme juridique : libellé explicite, sinon mot-clé dans la dénomination ou le texte.
    legal_hint = found.get("legal_form", {}).get("value")
    form = _detect_legal_form(pdf.pages, legal_hint or found.get("company_name", {}).get("value"))
    if form:
        label, page_no, snippet = form
        page_no = page_no or found.get("legal_form", found.get("company_name", {})).get("page", 1)
        found["legal_form"] = {"value": label, "source": source, "page": page_no, "snippet": (snippet or "")[:200]}

    return found


def analyse_document(data: bytes, source: str, max_bytes: int, max_pages: int) -> dict:
    """Point d'entrée : lit le PDF et renvoie un résumé exploitable par l'interface."""
    pdf = read_pdf(data, max_bytes=max_bytes, max_pages=max_pages)
    fields = extract_fields(pdf, source)
    return {
        "pages": pdf.page_count,
        "text_found": pdf.has_text,
        "fields": fields,
        "warning": None if pdf.has_text else
        "Aucun texte lisible : document scanné. Les informations sont à saisir manuellement.",
    }
