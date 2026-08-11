from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
import unicodedata
from abc import ABC, abstractmethod

from .models import SkillRecord


FIELD_WEIGHTS = {
    "aliases": 8.0,
    "when": 6.0,
    "description": 3.0,
    "capabilities": 2.0,
    "package": 1.0,
}
STOP = {"a", "an", "and", "for", "from", "in", "of", "or", "the", "to", "use", "when", "with"}
PLATFORM_TOKENS = {"windows", "linux", "macos", "mac", "darwin", "ios", "android"}
_INDEX_CACHE: list[tuple[list[SkillRecord], list[dict[str, list[str]]], list[set[str]], Counter[str]]] = []


class SearchBackend(ABC):
    """Stable extension point for future local search implementations."""

    @abstractmethod
    def rank(self, query: str, records: list[SkillRecord], *, profile: str = "") -> list[tuple[float, SkillRecord, tuple[str, ...]]]:
        raise NotImplementedError


class SparseSearchBackend(SearchBackend):
    def rank(self, query: str, records: list[SkillRecord], *, profile: str = "") -> list[tuple[float, SkillRecord, tuple[str, ...]]]:
        return hybrid_rank(query, records, profile=profile)


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def tokens(value: str) -> list[str]:
    value = normalize(value)
    latin = re.findall(r"[a-z0-9]+(?:[.+#-][a-z0-9]+)*", value)
    latin = [token for token in latin if token not in STOP]
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", value)
    cjk: list[str] = []
    for run in cjk_runs:
        cjk.extend(run)
        cjk.extend(run[i:i + 2] for i in range(max(0, len(run) - 1)))
        cjk.extend(run[i:i + 3] for i in range(max(0, len(run) - 2)))
    return latin + cjk


def ngrams(value: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    compact = re.sub(r"\s+", "", normalize(value))
    return {compact[i:i + size] for size in sizes for i in range(max(0, len(compact) - size + 1))}


def field_text(record: SkillRecord) -> dict[str, str]:
    return {
        "aliases": " ".join([record.name, *record.aliases]),
        "when": " ".join([record.when_to_use, *record.positive_triggers]),
        "description": record.description,
        "capabilities": " ".join([*record.actions, *record.artifacts, *record.input_types, *record.output_types]),
        "package": " ".join([record.package, record.provider]),
    }


def hybrid_rank(query: str, records: list[SkillRecord], *, profile: str = "") -> list[tuple[float, SkillRecord, tuple[str, ...]]]:
    eligible: list[SkillRecord] = []
    eligible_indices: list[int] = []
    query_norm = normalize(query)
    exact_flags = [_exact_named_normalized(record, query_norm) for record in records]
    for record_index, record in enumerate(records):
        explicit = exact_flags[record_index]
        if _policy_reason(record, query, query_norm, profile, explicit):
            continue
        eligible.append(record)
        eligible_indices.append(record_index)

    if _short_cjk_exact_only(query):
        filtered = [
            (record, index)
            for record, index in zip(eligible, eligible_indices)
            if exact_flags[index]
        ]
        eligible = [record for record, _ in filtered]
        eligible_indices = [index for _, index in filtered]

    if not eligible:
        return []

    q_tokens = tokens(query)
    all_doc_tokens, all_doc_ngrams, all_document_frequency = _sparse_index(records)
    doc_tokens = [all_doc_tokens[index] for index in eligible_indices]
    doc_ngrams = [all_doc_ngrams[index] for index in eligible_indices]
    n_docs = len(eligible)
    if n_docs == len(records):
        document_frequency = all_document_frequency
    else:
        document_frequency: Counter[str] = Counter()
        for fields in doc_tokens:
            document_frequency.update({token for values in fields.values() for token in values})

    bm25: list[tuple[float, int]] = []
    for index, fields in enumerate(doc_tokens):
        score = 0.0
        for field, values in fields.items():
            counts = Counter(values)
            length = max(1, len(values))
            weight = FIELD_WEIGHTS[field]
            for term in q_tokens:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1.0 + (n_docs - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
                score += weight * idf * ((tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / 40.0)))
        if eligible[index].package and eligible[index].package.replace("-", " ") in query_norm:
            score += 1.0
        bm25.append((score, index))

    query_ngrams = ngrams(query)
    char_rank: list[tuple[float, int]] = []
    for index, record in enumerate(eligible):
        candidate = doc_ngrams[index]
        similarity = len(query_ngrams & candidate) / max(1.0, math.sqrt(len(query_ngrams) * len(candidate)))
        char_rank.append((similarity, index))

    eligible_exact = [exact_flags[index] for index in eligible_indices]
    exact_rank = [(1.0 if eligible_exact[index] else 0.0, index) for index in range(len(eligible))]
    package_scores: dict[str, float] = defaultdict(float)
    for score, index in bm25:
        package = eligible[index].package
        if package:
            package_scores[package] = max(package_scores[package], score)
    top_packages = {package for package, _ in sorted(package_scores.items(), key=lambda item: (-item[1], item[0]))[:2]}
    package_rank = [(score if eligible[index].package in top_packages else 0.0, index) for score, index in bm25]
    rankings = [
        (3.0, _ordered(exact_rank, positive_only=True)),
        (1.0, _ordered(bm25, positive_only=True)),
        (0.8, _ordered(char_rank, positive_only=True)),
        (0.2, _ordered(package_rank, positive_only=True)),
    ]
    fused: defaultdict[int, float] = defaultdict(float)
    evidence: defaultdict[int, list[str]] = defaultdict(list)
    for weight, ranking in rankings:
        for rank, index in enumerate(ranking, 1):
            fused[index] += weight / (60.0 + rank)
            evidence[index].append(f"rank={rank}, weight={weight}")
    raw_bm25 = dict((index, score) for score, index in bm25)
    raw_char = dict((index, score) for score, index in char_rank)
    output = []
    for index, fused_score in fused.items():
        exact_bonus = 1.0 if eligible_exact[index] else 0.0
        score = fused_score * 100.0 + min(raw_bm25.get(index, 0.0), 20.0) * 0.05 + raw_char.get(index, 0.0) + exact_bonus
        reasons = [*evidence[index]]
        if exact_bonus:
            reasons.insert(0, "exact name or alias")
        output.append((round(score, 6), eligible[index], tuple(reasons)))
    output.sort(key=lambda item: (-item[0], item[1].name.lower(), item[1].id))
    return output


def policy_exclusions(query: str, records: list[SkillRecord], *, profile: str = "") -> dict[str, str]:
    excluded: dict[str, str] = {}
    query_norm = normalize(query)
    for record in records:
        explicit = _exact_named_normalized(record, query_norm)
        reason = _policy_reason(record, query, query_norm, profile, explicit)
        if reason:
            excluded[record.id] = reason
    return excluded


def _policy_reason(record: SkillRecord, query: str, query_norm: str, profile: str, explicit: bool) -> str:
    explicit_only = (
        record.requires_explicit_user_intent
        or not record.default_routable
        or record.route_mode in {"manual", "explicit-only"}
        or record.trigger_mode in {"manual", "explicit-only"}
    )
    if explicit_only and not explicit:
        return "explicit intent required"
    profile_only = record.route_mode == "profile-only" or record.trigger_mode == "profile-only"
    if profile_only and not explicit and not profile and not _strong_overlap(query, record):
        return "profile or strong match required"
    if record.trigger_mode == "strong-semantic-match" and not explicit and not _strong_overlap(query, record):
        return "strong semantic match required"
    if record.compatibility and profile and not _compatible(record.compatibility, profile):
        return "compatibility does not match routing profile"
    if any(_phrase_present(query_norm, value) for value in record.negative_triggers):
        return "negative trigger matched"
    return ""


def exact_named(record: SkillRecord, query: str) -> bool:
    return _exact_named_normalized(record, normalize(query))


def _exact_named_normalized(record: SkillRecord, query_norm: str) -> bool:
    for value in [record.name, *record.aliases]:
        candidate = normalize(value)
        has_cjk = bool(re.search(r"[\u3400-\u9fff]", candidate))
        if candidate and (candidate in query_norm if has_cjk else _bounded_contains(query_norm, candidate)):
            return True
    return False


def _bounded_contains(text: str, candidate: str) -> bool:
    start = text.find(candidate)
    while start >= 0:
        end = start + len(candidate)
        left_ok = start == 0 or not text[start - 1].isalnum()
        right_ok = end == len(text) or not text[end].isalnum()
        if left_ok and right_ok:
            return True
        start = text.find(candidate, start + 1)
    return False


def _short_cjk_exact_only(query: str) -> bool:
    normalized = normalize(query)
    cjk = re.findall(r"[\u3400-\u9fff]", normalized)
    latin = re.findall(r"[a-z0-9]", normalized)
    return bool(cjk) and len(cjk) < 3 and not latin


def _ordered(values: list[tuple[float, int]], *, positive_only: bool) -> list[int]:
    filtered = [(score, index) for score, index in values if not positive_only or score > 0]
    filtered.sort(key=lambda item: (-item[0], item[1]))
    return [index for _, index in filtered]


def _phrase_present(query: str, phrase: str) -> bool:
    return bool(phrase and normalize(phrase) in normalize(query))


def _strong_overlap(query: str, record: SkillRecord) -> bool:
    query_terms = set(tokens(query))
    candidate = set(tokens(" ".join([record.name, record.when_to_use, record.description])))
    return len(query_terms & candidate) >= 3


def _compatible(requirement: str, profile: str) -> bool:
    required = set(tokens(requirement))
    available = set(tokens(profile))
    required_platforms = required & PLATFORM_TOKENS
    available_platforms = available & PLATFORM_TOKENS
    if required_platforms and available_platforms and not required_platforms & available_platforms:
        return False
    return not required or bool(required & available)


def _sparse_index(records: list[SkillRecord]) -> tuple[list[dict[str, list[str]]], list[set[str]], Counter[str]]:
    for cached_records, cached_tokens, cached_ngrams, cached_df in _INDEX_CACHE:
        if cached_records is records:
            return cached_tokens, cached_ngrams, cached_df
    doc_tokens = [{field: tokens(text) for field, text in field_text(record).items()} for record in records]
    doc_ngrams = [ngrams(" ".join(field_text(record).values())) for record in records]
    document_frequency: Counter[str] = Counter()
    for fields in doc_tokens:
        document_frequency.update(set(token for values in fields.values() for token in values))
    _INDEX_CACHE.append((records, doc_tokens, doc_ngrams, document_frequency))
    if len(_INDEX_CACHE) > 4:
        _INDEX_CACHE.pop(0)
    return doc_tokens, doc_ngrams, document_frequency
