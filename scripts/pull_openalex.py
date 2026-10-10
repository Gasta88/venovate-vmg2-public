"""OpenAlex pull: group_by aggregations (year × country × institution).

Aggregations only, not full works — keeps snapshots tiny and cheap.
Prefer filter + group_by over search. Needs OPENALEX_API_KEY from .env.
Writes data/raw/openalex/<YYYY-MM-DD>/ via scripts/_common.write_snapshot().
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

# Same anchoring as _common._resolve_base: works from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "queries.yaml"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
# One group_by per request (OpenAlex limitation) → three marginal pulls per
# package; the year × country × institution cross-tab is assembled downstream
# from these marginals (decisions.md 2026-09-26 "OpenAlex aggregation-snapshot
# contract"). Script-owned: config/queries.yaml is NOT edited (AC-2).
DIMENSIONS = ("publication_year", "authorships.countries", "authorships.institutions.id")
PER_PAGE = 200
# Relevance sampling (issue-53 WI-02 / D-03, AC-4): per query × dimension, up
# to N group rows are LOGGED. OpenAlex snapshots are aggregation-only (the
# 2026-09-26 contract), so the sampled "records" are the group rows themselves;
# the underlying works stay unpulled. stdout only — file contracts untouched.
# Cut fallback: 10.
SAMPLE_SIZE = 20


def load_group_by_queries(config_path: Path = CONFIG_PATH) -> dict[str, list[str]]:
    """Per package, the queries.yaml openalex strings containing '&group_by='.

    Only group_by count strings are ever executed (AC-3/AC-4); plain filter
    strings are selected out here and never sent anywhere. Raises KeyError on
    a missing openalex section and ValueError if any package has zero
    group_by strings (queries.yaml contract guarantees ≥1/package — D-1).
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    section = cfg.get("openalex") if isinstance(cfg, dict) else None
    if not section:
        raise KeyError(f"missing 'openalex' section in {config_path}")
    selected: dict[str, list[str]] = {}
    for package, queries in section.items():
        group_by = [q for q in (queries or []) if "&group_by=" in q]
        if not group_by:
            raise ValueError(
                f"package {package!r} has no '&group_by=' query in {config_path}"
            )
        selected[package] = group_by
    return selected


def base_filter(query_string: str) -> str:
    """The config string stripped from '&group_by=' onward (FR-2).

    Returns the verbatim fragment *including* the leading 'filter=' key (e.g.
    'filter=title.search:"venom peptide",from_publication_date:2015-01-01').
    This is a parsed unit, never a param value — fetch_groups splits it into
    key/value, so a double prefix (filter=filter%3D…) cannot occur.
    """
    return query_string.split("&group_by=", 1)[0]


def fetch_groups(
    session: _common.ResilientClient,
    base: str,
    dim: str,
    *,
    api_key: str,
    mailto: str | None,
) -> tuple[pd.DataFrame, str]:
    """One group_by dimension's pages merged into a DataFrame (FR-3, AC-6).

    Cursor loop: first request `cursor=*`, then follow meta.next_cursor until
    falsy — correct whether or not the API paginates group_by arrays (a single
    page with null next_cursor degrades to one request). The base fragment is
    split on the first `=` so only the *value* reaches the `filter` param
    (httpx URL-encodes it; no double `filter=` prefix). api_key and optional
    mailto ride as per-request query params — never headers, never the client
    (decisions.md 2026-09-26 HTTP-stack ruling). Returns (df, first-page
    request URL) — the URL is what the manifest records; _common._redact_url
    masks api_key/mailto there.
    """
    filter_key, filter_value = base.split("=", 1)
    rows: list[dict] = []
    cursor: str | None = "*"
    first_url: str | None = None
    while cursor:
        params: dict[str, str] = {
            "group_by": dim,
            "per-page": str(PER_PAGE),
            "cursor": cursor,
            "api_key": api_key,
            filter_key: filter_value,
        }
        if mailto:
            params["mailto"] = mailto
        # Through ResilientClient: retry/backoff/Retry-After apply for free.
        response = session.get(OPENALEX_WORKS_URL, params=params)
        if first_url is None:
            first_url = str(response.url)
        if response.status_code != 200:
            # Name path only (no query string) — credentials never echoed (FR-7).
            raise RuntimeError(
                f"OpenAlex {response.status_code} for group_by={dim} on {response.url.path}"
            )
        payload = response.json()
        rows.extend(payload.get("group_by", []))
        cursor = payload.get("meta", {}).get("next_cursor")
    normalized = [
        # Defensive: some API eras omit display names — fall back to the key.
        {**entry, "key_display_name": entry.get("key_display_name") or entry.get("key")}
        for entry in rows
    ]
    df = pd.DataFrame(normalized, columns=["key", "key_display_name", "count"])
    if first_url is None:  # cursor="*" guarantees ≥1 iteration.
        raise AssertionError("unreachable: cursor loop always issues one request")
    return df, first_url


def pull_all(
    session: _common.ResilientClient,
    *,
    base_dir: Path | str | None = None,
    config_path: Path = CONFIG_PATH,
) -> list[Path]:
    """Package × group_by string × dimension → snapshot+manifest pairs (FR-5).

    The testable seam: base_dir=None uses write_snapshot's production default
    (real data/raw/); gates pass a temp dir. The manifest `query` field records
    the config string verbatim (queries.yaml content contract, decisions.md
    2026-09-26); stems are f"{package}-{dim}" so one pull writes a distinct
    pair per dimension (AC-1/AC-7). FileExistsError on a same-day collision
    propagates — loud, never a silent overwrite.
    """
    api_key = session.api_key_openalex
    if api_key is None:  # main() guards first; direct callers must not slip past.
        raise RuntimeError("OPENALEX_API_KEY missing: session.api_key_openalex is None")
    mailto = _common.contact_email()
    paths: list[Path] = []
    for package, query_strings in load_group_by_queries(config_path).items():
        for query_string in query_strings:
            base = base_filter(query_string)
            for dim in DIMENSIONS:
                df, url = fetch_groups(session, base, dim, api_key=api_key, mailto=mailto)
                logging.info("openalex relevance sample — package %r dim %s (%d groups, first %d shown):",
                             package, dim, len(df), min(SAMPLE_SIZE, len(df)))  # WI-02
                for rec in df.head(SAMPLE_SIZE).itertuples(index=False):
                    logging.info("  %s | %s | %s", rec.key, rec.key_display_name, rec.count)
                paths.append(
                    _common.write_snapshot(
                        df,
                        "openalex",
                        query_string,
                        url=url,
                        name=f"{package}-{dim}",
                        base_dir=base_dir,
                    )
                )
    return paths


def main() -> int:
    """Entry point — order matters: hard-fail BEFORE any request (AC-8/FR-6)."""
    logging.basicConfig(level=logging.INFO)
    session = _common.get_session()  # loads .env (idempotent); no request yet.
    try:
        if session.api_key_openalex is None:
            print(
                "ERROR: OPENALEX_API_KEY is not set. Copy .env.example to .env "
                "and add your OpenAlex key.",
                file=sys.stderr,
            )
            return 1
        paths = pull_all(session)
    finally:
        session.close()
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
