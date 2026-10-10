"""Shared helpers: HTTP session with retries, write_snapshot() + manifest.

Contract (implements the plan previously sketched here):
- get_session(): HTTP session with retry/backoff and auth via .env
  (OPENALEX_API_KEY, CONTACT_EMAIL — see .env.example; .env stays gitignored).
- write_snapshot(df_or_data, source, query) -> data/raw/<source>/<YYYY-MM-DD>/…
- one manifest per pull, next to the snapshot: query, URL (credential params
  redacted), timestamp, record count, sha256 of the snapshot file bytes.

Verification gates live in scripts/check_common.py (run: `uv run python
scripts/check_common.py`). No network or env reads happen at import time;
auth is resolved when get_session() is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
import pandas as pd
from dotenv import load_dotenv

# Query params replaced with *** in manifest URLs — data/raw/ is committed and
# the repo goes public at submission, so credentials/PII must never be written.
REDACT_PARAMS = {"api_key", "key", "token", "mailto"}


def contact_email() -> str | None:
    """CONTACT_EMAIL from the environment; falsy/blank treated as unset."""
    return os.environ.get("CONTACT_EMAIL", "").strip() or None


def api_key_openalex() -> str | None:
    """OPENALEX_API_KEY from the environment; falsy/blank treated as unset.

    Never attached to requests globally — pull_openalex.py passes it as a
    query param itself via session.api_key_openalex (see get_session).
    """
    return os.environ.get("OPENALEX_API_KEY", "").strip() or None


def _utc_now() -> datetime:
    """Single UTC clock read — the date dir and file stem must derive from it."""
    return datetime.now(timezone.utc)


# --- HTTP session with retry/backoff ------------------------------------------------

MAX_RETRIES = 5  # 6 attempts total
BACKOFF_BASE = 1.0  # seconds
BACKOFF_CAP = 120.0  # seconds — increased for APIs with longer rate limit windows
RETRY_AFTER_CAP = 120.0  # honored Retry-After delay is capped at the same band
RETRY_STATUSES = frozenset({429})  # plus all 5xx (checked in _is_retryable_status)


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff min(BACKOFF_CAP, 1 * 2**attempt) seconds with ±10% jitter."""
    delay = min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt)
    return delay * random.uniform(0.9, 1.1)


def _retry_after_delay(response: httpx.Response) -> float | None:
    """Server-sent Retry-After (integer seconds) capped at the RETRY_AFTER_CAP band, else None.

    Edge case: HTTP-date or non-integer values are ignored and the computed
    backoff is used instead (documented in the spec's Test Strategy).
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = int(raw.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(RETRY_AFTER_CAP, float(seconds))


def _is_retryable_status(status_code: int) -> bool:
    return status_code in RETRY_STATUSES or 500 <= status_code <= 599


class ResilientClient:
    """Thin retrying wrapper around httpx.Client (context manager + get/post/request).

    Retry policy (spec AC-2): up to MAX_RETRIES retries on transport errors and
    status {429} ∪ 5xx; exponential backoff 1s–BACKOFF_CAP ±10% jitter; integer
    Retry-After overrides the computed backoff. Non-retryable statuses (4xx
    except 429) return normally — the caller raises. After the final attempt
    the last 429/5xx response is returned; transport errors are re-raised.
    """

    def __init__(self, client: httpx.Client, *, api_key_openalex: str | None = None):
        self._client = client
        # Exposed for pull_openalex.py to pass as a query param; never attached
        # globally (would leak into every source's requests and manifests).
        self.api_key_openalex = api_key_openalex

    def __getattr__(self, item):
        # Forward unknown attributes (base_url, headers, …) to the inner client.
        return getattr(self._client, item)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(_backoff_delay(attempt))
                continue
            if not _is_retryable_status(response.status_code) or attempt == MAX_RETRIES:
                return response
            delay = _retry_after_delay(response)
            if delay is None:
                delay = _backoff_delay(attempt)
            time.sleep(delay)
        raise AssertionError("unreachable: retry loop always returns or raises")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ResilientClient":
        self._client.__enter__()
        return self

    def __exit__(self, *exc_info) -> None:
        self._client.__exit__(*exc_info)


def get_session(*, timeout: float = 30.0) -> ResilientClient:
    """HTTP client with retry/backoff and `.env`-sourced auth (see module docstring).

    Loads .env via python-dotenv (idempotent; existing env vars win). Auth:
    CONTACT_EMAIL identifies the caller via User-Agent (polite-pool convention);
    omitted gracefully when unset so ctgov/cordis (no auth) never fail. The
    OpenAlex key is exposed as session.api_key_openalex, not attached globally.
    """
    load_dotenv()
    headers = {}
    email = contact_email()
    if email:
        headers["User-Agent"] = f"venovate-vmg2/0.1 (+mailto:{email})"
    client = httpx.Client(headers=headers, timeout=timeout)
    return ResilientClient(client, api_key_openalex=api_key_openalex())


# --- Snapshot writer -----------------------------------------------------------------


def _resolve_base(base_dir: Path | str | None) -> Path:
    """Snapshot root: explicit base_dir as given (relative → cwd), else repo-root
    data/raw anchored from this file's location so scripts work from any cwd."""
    if base_dir is not None:
        return Path(base_dir)
    return Path(__file__).resolve().parents[1] / "data" / "raw"


def _validate_source(source: str) -> None:
    """Path-traversal guard on a caller-supplied directory component."""
    if not source or Path(source).name != source or source in {".", ".."}:
        raise ValueError(f"invalid source name {source!r} (no path separators allowed)")


def _sha256_file(path: Path) -> str:
    """sha256 hex digest over the snapshot file bytes as written (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _redact_url(url: str) -> str:
    """Replace credential/PII query param values with *** and strip userinfo (FR-3, hard rule).

    data/raw/ is committed and the repo goes public at submission — manifests
    must never carry live keys or the contact email. Values are re-emitted
    as-is (no re-encoding) so only the redaction changes byte-for-byte.
    """
    parts = urlsplit(url)
    # Always strip userinfo (user:pass@) from netloc — data/raw/ is committed.
    netloc = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
    if not parts.query:
        return urlunsplit((parts.scheme, netloc, parts.path, "", parts.fragment))
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted = "&".join(
        f"{key}={'***' if key.lower() in REDACT_PARAMS else value}" for key, value in pairs
    )
    return urlunsplit((parts.scheme, netloc, parts.path, redacted, parts.fragment))


def write_snapshot(
    df_or_data,
    source: str,
    query: str,
    *,
    url: str | None = None,
    base_dir: Path | str | None = None,
    name: str | None = None,
) -> Path:
    """Write one pull snapshot + manifest under data/raw/<source>/<YYYY-MM-DD>/.

    df_or_data: pandas DataFrame → CSV; list[dict]/dict → JSONL; anything else
    → TypeError. Returns the snapshot Path. Never overwrites an existing
    snapshot (FileExistsError); pass name= to control the file stem.
    """
    _validate_source(source)
    if name is not None:
        _validate_source(name)
    base = _resolve_base(base_dir)

    # Single UTC clock read: the date dir and the stem must agree (midnight edge).
    now = _utc_now()
    date_dir = base / source / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    stem = name if name is not None else f"{source}-{now.strftime('%Y%m%dT%H%M%SZ')}"

    if isinstance(df_or_data, pd.DataFrame):
        fmt, suffix = "csv", ".csv"
        record_count = len(df_or_data)
    elif isinstance(df_or_data, dict):
        fmt, suffix = "jsonl", ".jsonl"
        df_or_data = [df_or_data]  # single record
        record_count = 1
    elif isinstance(df_or_data, list):
        fmt, suffix = "jsonl", ".jsonl"
        record_count = len(df_or_data)
    else:
        raise TypeError(
            f"df_or_data must be a pandas DataFrame, list, or dict — got {type(df_or_data).__name__}"
        )

    snapshot_path = date_dir / f"{stem}{suffix}"
    if snapshot_path.exists():
        # FR-4: fail loud rather than silently overwrite a committed snapshot.
        raise FileExistsError(f"snapshot already exists: {snapshot_path}")

    if fmt == "csv":
        # lineterminator="\n": byte-identical snapshots across OSes, so the
        # manifest sha256 is re-derivable on any machine (AC-7 / extdocs gotcha).
        df_or_data.to_csv(snapshot_path, index=False, encoding="utf-8", lineterminator="\n")
    else:
        with open(snapshot_path, "w", encoding="utf-8") as f:
            for record in df_or_data:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Step 5: one manifest per pull, next to the snapshot (stem-pair convention,
    # decisions.md 2026-09-26) so two same-day pulls never collide.
    manifest = {
        "source": source,
        "query": query,
        "url": _redact_url(url) if url is not None else None,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record_count": record_count,
        "sha256": _sha256_file(snapshot_path),
        "snapshot": snapshot_path.name,
        "format": fmt,
    }
    manifest_path = date_dir / f"{stem}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return snapshot_path