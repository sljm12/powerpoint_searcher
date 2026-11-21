"""Measure deck-group recall and best-slide relevance on labelled synthetic queries."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from check_search_workflow import query, submit, synthetic_index, wait_jobs
from search_service.embeddings import EmbeddingError, Embeddings
from search_service.models import SearchRequest, WarningDetail
from search_service.settings import Settings
from search_service.store import SearchStore


@dataclass
class Metrics:
    queries: int = 0
    degraded: int = 0
    relevant: int = 0
    retrieved: int = 0
    returned_relevant_decks: int = 0
    relevant_best_slides: int = 0
    reciprocal_rank: float = 0

    def add(self, groups: list[dict], relevant: set[tuple[str, int]], degraded: bool) -> None:
        self.queries += 1
        self.degraded += degraded
        self.relevant += len(relevant)
        relevant_decks = {deck for deck, _ in relevant}
        first_rank = next((rank for rank, group in enumerate(groups, 1)
                           if group["deck_id"] in relevant_decks), None)
        if first_rank:
            self.reciprocal_rank += 1 / first_rank
        for group in groups[:5]:
            deck_id = group["deck_id"]
            first = (deck_id, group["matches"][0]["slide_number"])
            self.retrieved += first in relevant
            self.returned_relevant_decks += deck_id in relevant_decks
            self.relevant_best_slides += first in relevant

    def summary(self) -> dict[str, int | float]:
        return {"queries": self.queries, "degraded": self.degraded,
                "recall_at_5": self.retrieved / max(1, self.relevant),
                "relevant_first_slide": self.relevant_best_slides / max(1, self.returned_relevant_decks),
                "mrr": self.reciprocal_rank / max(1, self.queries)}


async def compare_paths(policy: dict, cases: list[dict], known: dict[str, str]) -> dict:
    settings = Settings.from_environment()
    store = SearchStore(settings)
    embeddings = Embeddings(settings)
    scopes = [UUID(deck_id) for deck_id in known.values()]
    metrics = {name: Metrics() for name in ("lexical", "vector", "fused")}
    try:
        with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5, row_factory=dict_row,
                             options="-c default_transaction_read_only=on") as connection:
            store.db = connection
            active_library, active_profile, maintenance = store.profile_state()
            if maintenance or str(active_library) != policy["library_id"] or active_profile != policy["profile_id"]:
                raise RuntimeError("Search database and live API profiles differ.")
            warning = WarningDetail(code="comparison", message="Intentional lexical comparison.")
            for case in cases:
                relevant = {(known[item["deck"]], item["slide"]) for item in case["relevant"]}
                request = SearchRequest(library_id=active_library, profile_id=active_profile,
                                        query=case["text"], limit=100)
                try:
                    vector = (await asyncio.wait_for(embeddings.embed(
                        [settings.query_prefix + case["text"]], retry_indexing=False
                    ), timeout=min(settings.query_timeout, settings.embedding_timeout)))[0]
                except (EmbeddingError, TimeoutError):
                    vector = None
                lexical = store.search(request, None, warning, deadline=time.monotonic() + 5, deck_ids=scopes)
                metrics["lexical"].add(lexical.model_dump(mode="json")["decks"], relevant, False)
                if vector is None:
                    for name in ("vector", "fused"):
                        metrics[name].add([], relevant, True)
                    continue
                for name in ("vector", "fused"):
                    result = store.search(request, vector, None, deadline=time.monotonic() + 5,
                                          deck_ids=scopes, semantic_only=name == "vector")
                    metrics[name].add(result.model_dump(mode="json")["decks"], relevant, False)
    finally:
        await embeddings.close()
    return {name: outcome.summary() for name, outcome in metrics.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8502")
    parser.add_argument("--held-out", action="store_true")
    parser.add_argument("--real-endpoint", action="store_true")
    parser.add_argument("--compare-paths", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    options = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "held-out-text-search.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    language_by_deck = {deck["id"]: deck["language"] for deck in fixture["decks"]}
    cases = fixture["queries"]
    languages = {case["language"] for case in cases}
    cross_language = sum(any(language_by_deck[item["deck"]] != case["language"]
                             for item in case["relevant"]) for case in cases)
    assert len(cases) >= 30 and len(languages) >= 3 and cross_language >= 10
    if options.self_check:
        sample = Metrics()
        sample.add([{"deck_id": "x", "matches": [{"slide_number": 1}]}], {("x", 1)}, False)
        assert sample.summary()["recall_at_5"] == 1
        assert sample.summary()["mrr"] == 1
        print(json.dumps({"queries": len(cases), "languages": sorted(languages), "cross_language": cross_language}))
        return
    if not options.held_out:
        parser.error("Use --held-out to run the labelled suite.")
    metrics = {key: Metrics() for key in ["all", "cross_language", *sorted(languages)]}
    fallback_samples = []
    comparison = None
    with synthetic_index(options.base_url, options.real_endpoint) as (client, policy, deck_ids):
        jobs = [submit(client, policy, deck_ids, deck["slides"]) for deck in fixture["decks"]]
        known = dict(zip(language_by_deck, deck_ids, strict=True))
        wait_jobs(client, jobs, 300)
        for number, case in enumerate(cases, 1):
            relevant = {(known[item["deck"]], item["slide"]) for item in case["relevant"]}
            start = time.monotonic()
            result = query(client, policy, case["text"], limit=100)
            if result["next_offset"] is not None:
                raise RuntimeError("The held-out comparison requires a corpus that fits in one result page.")
            if result["degraded"]:
                fallback_samples.append({"case": number, "language": case["language"],
                                         "seconds": round(time.monotonic() - start, 3),
                                         "warnings": [warning["code"] for warning in result["warnings"]]})
            groups = [group for group in result["decks"] if group["deck_id"] in deck_ids]
            for key in ("all", case["language"]):
                metrics[key].add(groups, relevant, result["degraded"])
            if any(language_by_deck[item["deck"]] != case["language"] for item in case["relevant"]):
                metrics["cross_language"].add(groups, relevant, result["degraded"])
        if options.compare_paths:
            comparison = asyncio.run(compare_paths(policy, cases, known))
    report = {key: value.summary() for key, value in metrics.items()}
    print(json.dumps({"metrics": report, "fallback_samples": fallback_samples, "scoped_comparison": comparison},
                     indent=2))
    all_cases = report["all"]
    if all_cases["recall_at_5"] < 0.85 or all_cases["relevant_first_slide"] < 0.80:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
