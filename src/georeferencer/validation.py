import re
import hashlib

from .models import Document, Extraction, Mention
from .coordinates import coordinate_evidence_matches


def evidence_errors(extraction: Extraction, document: Document) -> list[str]:
    errors = []
    digest = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    # Collapse only whitespace, retaining a map back to the original characters.
    pieces = list(re.finditer(r"\s+|\S", document.text))
    canonical = "".join(" " if m.group().isspace() else m.group() for m in pieces)
    def whitespace(value):
        return re.sub(r"\s+", " ", value)

    def check(mention: Mention, label: str):
        # Resolve text anchors ourselves; do not rely on LLM-generated offsets.
        mention.start = mention.end = mention.document_sha256 = None
        matches = []
        quote = whitespace(mention.quote)
        # Models sometimes return context around the name inside a full-clause
        # quote. Remove that redundant context only for a unique verbatim quote.
        if canonical.count(quote) == 1 and (not mention.prefix or whitespace(mention.prefix) in quote) and (not mention.suffix or whitespace(mention.suffix) in quote):
            mention.prefix = mention.suffix = None
        position = canonical.find(quote)
        while position != -1:
            end = position + len(quote)
            if (not mention.prefix or canonical[:position].endswith(whitespace(mention.prefix))) and (not mention.suffix or canonical[end:].startswith(whitespace(mention.suffix))):
                matches.append((pieces[position].start(), pieces[end - 1].end()))
            position = canonical.find(quote, position + 1)
        if not matches:
            if quote not in canonical:
                reason = "quote text was not found, even after whitespace normalization"
            else:
                reason = "quote exists, but the supplied prefix/suffix do not match immediately adjacent text"
            errors.append(f"{label}: quote and context do not match document text: {reason}")
        elif len(matches) > 1:
            errors.append(f"{label}: ambiguous quote; provide immediate prefix/suffix context")
        else:
            mention.start, mention.end = matches[0]
            mention.document_sha256 = digest
            mention.quote = document.text[mention.start:mention.end]

    for entity in extraction.entities:
        anchored, seen = [], set()
        for mention in entity.mentions:
            # Already anchored by Python in this exact document (e.g. a mapped
            # chunk occurrence). Do not expand it across unrelated chunks.
            if (mention.document_sha256 == digest and mention.start is not None and
                mention.end is not None and document.text[mention.start:mention.end] == mention.quote):
                if (mention.start, mention.end) not in seen:
                    seen.add((mention.start, mention.end)); anchored.append(mention)
                continue
            quote = whitespace(mention.quote)
            position = canonical.find(quote) if quote.strip() else -1
            if position == -1:
                mention.start = mention.end = mention.document_sha256 = None
                errors.append(f"{entity.id}: location expression was not found in document text")
                anchored.append(mention)
                continue
            first = True
            while position != -1:
                end = position + len(quote)
                start_char, end_char = pieces[position].start(), pieces[end-1].end()
                if (start_char, end_char) not in seen:
                    occurrence = mention if first else mention.model_copy(deep=True)
                    first = False
                    occurrence.start, occurrence.end = start_char, end_char
                    occurrence.quote = document.text[start_char:end_char]
                    occurrence.document_sha256 = digest
                    occurrence.prefix = document.text[max(0, start_char-120):start_char] or None
                    occurrence.suffix = document.text[end_char:end_char+120] or None
                    seen.add((start_char,end_char)); anchored.append(occurrence)
                position = canonical.find(quote, position+1)
        entity.mentions = anchored
        for claim in entity.coordinate_claims:
            check(claim.evidence, entity.id)
            if claim.crs_evidence:
                check(claim.crs_evidence, entity.id)
            if not coordinate_evidence_matches(claim):
                errors.append(f"{entity.id}: coordinate numbers are not present in evidence")
    for relation in extraction.relationships:
        check(relation.evidence, f"relationship {relation.subject}/{relation.object}")
    return errors


def normalize(value: str | None) -> str:
    import unicodedata
    folded = ''.join(c for c in unicodedata.normalize('NFKD',(value or '').casefold()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", folded))
