# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Trigram similarity identical to PostgreSQL's pg_trgm `similarity()`.

Used for fuzzy company-name matching. When the database has pg_trgm the same value can be
computed in SQL (with a GIN index); otherwise this exact Python version is used, so results
do not depend on which one ran.

pg_trgm: lower-case, split into words of letters/digits, pad each word with two spaces in front
and one behind, collect the 3-character windows, and score |A and B| / |A or B|.
"""

import math
import re
import unicodedata

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in _WORD.findall(text.lower()):
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def similarity(a: str, b: str) -> float:
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ----------------------------------------------------------------------------- company names
# Company-name matching differs from the plain pg_trgm maths in two ways (BUG-P2-10, BUG-P2-11):
#   * legal-form words (Pvt, Private, Ltd, Limited, LLP ...) are ignored, so "Acme Foods Pvt Ltd"
#     and "Acme Foods Private Limited" are the same company;
#   * a word keeps its combining marks (Indic vowel signs and viram), so Hindi/Tamil names are
#     compared as words, not as single letters.
# One Python implementation is the source of truth: the same trigrams are stored per account
# (`accounts.name_grams`, GIN-indexed) to prefilter candidates, and the score is computed here.
LEGAL_WORDS = frozenset(
    {"pvt", "private", "ltd", "limited", "llp", "llc", "inc", "corp", "corporation", "plc", "opc"}
)


def _name_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    words: list[str] = []
    cur: list[str] = []
    for ch in text:
        if (ch.isalnum() and ch != "_") or unicodedata.category(ch).startswith("M"):
            cur.append(ch)
        elif cur:
            words.append("".join(cur))
            cur = []
    if cur:
        words.append("".join(cur))
    return words


def name_key(name: str) -> str:
    words = _name_words(name)
    kept = [w for w in words if w not in LEGAL_WORDS]
    return " ".join(kept or words)


def _units(word: str) -> list[str]:
    """Split a word into letters, keeping combining marks with their letter and joining a letter
    that follows a virama (conjunct consonants), so an Indic word is a few akshara units, not many
    single code points."""
    units: list[str] = []
    joined = False  # previous unit ended with a virama
    for ch in word:
        if units and (unicodedata.category(ch).startswith("M") or joined):
            units[-1] += ch
        else:
            units.append(ch)
        joined = unicodedata.combining(ch) == 9
    return units


def name_trigrams(name: str) -> set[str]:
    grams: set[str] = set()
    for word in name_key(name).split():
        padded = [" ", " ", *_units(word), " "]
        grams.update("".join(padded[i : i + 3]) for i in range(len(padded) - 2))
    return grams


def name_grams(name: str) -> list[str]:
    """The stored, sorted gram list for an account name (cap keeps the row small)."""
    return sorted(name_trigrams(name))[:400]


def grams_similarity(ta: set[str] | frozenset[str], tb: set[str] | frozenset[str]) -> float:
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def name_similarity(a: str, b: str) -> float:
    return grams_similarity(name_trigrams(a), name_trigrams(b))


def prefilter_grams(
    grams: set[str], threshold: float, common: frozenset[str] = frozenset()
) -> list[str]:
    """Grams to probe the GIN index with. A name that reaches `threshold` shares at least
    ceil(threshold * |A|) grams with the query A, so it must share one of ANY
    |A| - ceil(threshold * |A|) + 1 of them (prefix filtering). The rarest go first: grams the
    database reports as common (`common`) and the space-padded ones come last."""
    if not grams:
        return []
    need = math.ceil(threshold * len(grams) - 1e-9)
    take = len(grams) - need + 1
    ordered = sorted(grams, key=lambda g: (g in common, " " in g, g))
    return ordered[:take]
