"""ClinicalTrials.gov API v2 pull. No API key required.

Iterates every ctgov entry in config/queries.yaml (source → package → query
string), fetches matching studies from the public v2 endpoint following
pageToken/nextPageToken cursors, dedupes by NCT ID per package, and writes one
JSONL snapshot + manifest per package under data/raw/ctgov/<YYYY-MM-DD>/ via
scripts/_common.write_snapshot().

Response envelope (live-verified 2026-09-28, specs/issue-6-extdocs.md):
{"studies": [...], "nextPageToken": "<opaque>"} — the top-level key is
`studies`, NOT `entries`. No .env required: get_session() omits the
User-Agent header gracefully when CONTACT_EMAIL is unset. Verification gates
live in scripts/check_ctgov.py (run: `uv run python scripts/check_ctgov.py`).
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

# Same anchoring as _common._resolve_base / pull_openalex.py: works from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "queries.yaml"
CTGOV_STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
PAGE_SIZE = 1000  # live-verified: current term sets fit one page (~40 records)
# Relevance sampling (issue-53 WI-02 / D-03, AC-4): per query, the first N
# fetched records are LOGGED (briefTitle + nctId) so the operator can verify
# the query measures what the report's claim asserts. stdout only — snapshots,
# manifests and the stem-pair file contract are untouched (the 2026-10-04
# supersessions do not open the manifest shape). Cut fallback: 10.
SAMPLE_SIZE = 20


def log_relevance_sample(package: str, idx: int, studies: list[dict]) -> None:
    """Log up to SAMPLE_SIZE record titles for one query (WI-02 audit trail)."""
    sample = studies[:SAMPLE_SIZE]
    logging.info("ctgov relevance sample — package %r query #%d (%d/%d records):",
                 package, idx + 1, len(sample), len(studies))
    for study in sample:
        ident = (study.get("protocolSection") or {}).get("identificationModule") or {}
        logging.info("  %s | %s", ident.get("nctId", "?"),
                     str(ident.get("briefTitle", ""))[:120])


def load_ctgov_queries(config_path: Path = CONFIG_PATH) -> dict[str, list[str]]:
    """Per package, the queries.yaml ctgov strings verbatim (FR1/AC2).

    Raises KeyError on a missing ctgov section and ValueError if any package
    list is empty (queries.yaml contract guarantees non-empty cells —
    decisions.md 2026-09-26 "queries.yaml content contract"; mirrors the guard
    in pull_openalex.load_group_by_queries). Strings are self-contained
    complete fragments: passed through verbatim, never re-encoded.
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    section = cfg.get("ctgov") if isinstance(cfg, dict) else None
    if not section:
        raise KeyError(f"missing 'ctgov' section in {config_path}")
    queries: dict[str, list[str]] = {}
    for package, strings in section.items():
        if not strings:
            raise ValueError(f"package {package!r} has no ctgov query in {config_path}")
        queries[package] = list(strings)
    return queries


def fetch_studies(
    session: _common.ResilientClient, query_string: str
) -> tuple[list[dict], str]:
    """All pages for ONE query string, merged in page order (AC3, FR3/FR4).

    The config fragment is embedded verbatim as a URL string — never re-quoted
    or split into a params dict — so AREA[...], spaces, quotes, and the
    trailing &filter.overallStatus filter pass through untouched (httpx
    normalizes reserved chars to percent-encoding, which is semantically
    identical; live-verified 2026-09-28). Later pages append ONLY the opaque
    token via URL.copy_merge_params — passing `params=` to get() would REPLACE
    the whole query string in httpx 0.28 (verified; see progress.md § Errors),
    silently dropping the verbatim fragment. Termination: v2 has no usable
    totalCount — follow nextPageToken until falsy. Through ResilientClient, so
    429/5xx backoff is inherited (AC7: no hand-rolled retries). Returns
    (studies, first-page request URL) — the envelope key is `studies`, NOT
    `entries` (specs/issue-6-extdocs.md CRITICAL finding, live-verified
    2026-09-28).
    """
    base_url = httpx.URL(f"{CTGOV_STUDIES_URL}?{query_string}&pageSize={PAGE_SIZE}")
    studies: list[dict] = []
    first_url: str | None = None
    token: str | None = "*"  # sentinel: guarantees ≥1 iteration; never sent
    while token:
        # First page: the verbatim URL, no pageToken at all. Later pages: same
        # URL with only the token merged in (encoded by httpx, fragment kept).
        target = base_url if first_url is None else base_url.copy_merge_params(
            {"pageToken": token}
        )
        response = session.get(str(target))
        if first_url is None:
            first_url = str(response.url)  # manifest URL (AC6), like pull_openalex
        if response.status_code != 200:
            # Name path only (no query string) — credential-free message idiom
            # from pull_openalex.py:104-108.
            raise RuntimeError(f"ctgov {response.status_code} for {response.url.path}")
        payload = response.json()
        studies.extend(payload.get("studies", []))
        token = payload.get("nextPageToken")
    if first_url is None:  # unreachable: the sentinel loop always issues one GET.
        raise AssertionError("unreachable: page loop always issues one request")
    return studies, first_url


def pull_all(
    session: _common.ResilientClient,
    *,
    base_dir: Path | str | None = None,
    config_path: Path = CONFIG_PATH,
) -> tuple[list[Path], list[str]]:
    """Package → fetch+dedupe all its queries → one snapshot+manifest (FR3/FR5).

    The testable seam: base_dir=None uses write_snapshot's production default
    (real data/raw/); gates pass a temp dir. Dedupe key is
    protocolSection.identificationModule.nctId, first occurrence wins,
    insertion order kept (AC5); records without an nctId are kept appended
    after the deduped ones and counted in a debug log (raw fidelity: never
    silently drop a study — the v2 schema always carries nctId, defensive edge).
    The try/except wraps ONLY the fetch phase (decisions.md 2026-09-28
    failure-boundary ruling): a fetch failure warns and continues;
    write_snapshot — called outside the try — lets FileExistsError (same-day
    collision, AC8) and TypeError propagate loudly. Returns
    (written paths, failed package names).
    """
    paths: list[Path] = []
    failures: list[str] = []
    for package, query_strings in load_ctgov_queries(config_path).items():
        merged: dict[str, dict] = {}
        no_id: list[dict] = []
        first_url: str | None = None
        manifest_query = query_strings[0]
        try:  # fetch phase only — see decisions.md 2026-09-28.
            for idx, query_string in enumerate(query_strings):
                page_studies, url = fetch_studies(session, query_string)
                log_relevance_sample(package, idx, page_studies)  # WI-02
                if first_url is None:
                    first_url = url  # AC6: manifest url = first request of package
                for study in page_studies:
                    nct_id = (
                        ((study.get("protocolSection") or {}).get("identificationModule") or {})
                    ).get("nctId")
                    if nct_id:
                        merged.setdefault(nct_id, study)  # first occurrence wins
                    else:
                        no_id.append(study)
                # Rate limit prevention: delay between queries within same package
                if idx < len(query_strings) - 1:
                    logging.info("ctgov: waiting 5s before next query (rate limit prevention)")
                    time.sleep(5)
        except Exception as exc:  # noqa: BLE001 — package boundary degrades (FR5/AC9)
            logging.warning("ctgov package %r failed: %s", package, exc)
            failures.append(package)
            continue
        studies = list(merged.values()) + no_id
        if no_id:
            logging.debug(
                "ctgov package %r: %d record(s) without nctId kept", package, len(no_id)
            )
        # OUTSIDE the try: a same-day collision must abort, never degrade (AC8).
        paths.append(
            _common.write_snapshot(
                studies,
                "ctgov",
                manifest_query,
                url=first_url,
                name=package,
                base_dir=base_dir,
            )
        )
    return paths, failures


def main(
    *,
    session: _common.ResilientClient | None = None,
    base_dir: Path | str | None = None,
    config_path: Path = CONFIG_PATH,
) -> int:
    """Entry point. The session=/base_dir=/config_path= kwargs are the
    in-process gate seam (decisions.md 2026-09-28 testability pattern).

    No API key and no .env required (AC1): get_session() omits the User-Agent
    header gracefully when CONTACT_EMAIL is unset. Exit code: 0 if every
    package succeeded, 1 if any failed (FR6/AC9). A same-day collision
    propagates FileExistsError as a traceback — loud, never silent (AC8).
    """
    logging.basicConfig(level=logging.INFO)
    own_session = session is None
    http = session or _common.get_session()
    try:
        paths, failures = pull_all(http, base_dir=base_dir, config_path=config_path)
    finally:
        if own_session:  # injected sessions belong to the caller (gates).
            http.close()
    for path in paths:
        print(path)
    if failures:
        logging.warning("%d ctgov package(s) failed: %s", len(failures), ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
