"""CORDIS bulk project CSV pull (H2020 + Horizon Europe). No API key required.

Downloads the two free bulk CSV distributions anchored to the cordis comment
block of config/queries.yaml (dataset pages on data.europa.eu — source of
truth), filters projects LOCALLY per package with the D-4 match rule (ANY
comma-separated keyword, case-insensitive substring, in title OR objective),
and writes one CSV snapshot + manifest pair per package x dataset via
scripts/_common.write_snapshot() into data/raw/cordis/<YYYY-MM-DD>/ — 10
stem-pairs per run (5 packages x 2 datasets, stems ``<package>-h2020`` /
``<package>-he``). A zero-match package still writes a header-only snapshot
(record_count 0). Never overwrites: a same-day re-run aborts loud with
FileExistsError.

Live-verified 2026-09-28 (specs/issue-7-extdocs.md): both distributions are
application/zip archives served from cordis.europa.eu; the project table is
the ``project.csv`` member — ``;``-delimited, UTF-8 (no BOM), all fields
quoted, 22 columns shared between the two files. ~0.8% of records carry
doubled/unescaped quotes in the free-text fields that break naive CSV
quoting, so the reader runs an anchor-repair pass (rejoining fragments
split on ';' between objective and contentUpdateDate) before handing the
CSV to pandas. HTTP goes through _common.get_session() (retry/backoff
policy inherited); the body streams to a temp file (never buffered whole
in RAM) and temps are cleaned in finally.

Verification gates live in scripts/check_cordis.py (run:
`uv run python scripts/check_cordis.py`). No network or env reads happen
at import time.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import sys
import tempfile
import time
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

import httpx
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

# Same anchoring as _common._resolve_base / pull_ctgov.py: works from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "queries.yaml"

# Direct CSV distribution URLs — live-verified 2026-09-28, cross-referenced
# to the cordis comment block of config/queries.yaml (lines 110-113, the
# dataset-page source of truth):
#   H2020:           https://data.europa.eu/data/datasets/cordish2020projects
#   Horizon Europe:  https://data.europa.eu/data/datasets/cordis-eu-research-projects-under-horizon-europe-2021-2027
# The portal is a client-side SPA that 200s on any path, so these were
# resolved from the DCAT-AP metadata (dcat:downloadURL of the mediaType csv
# "… Projects" distributions) and content-verified (application/zip, tens
# of MB, project.csv parses). Full evidence: specs/issue-7-extdocs.md.
H2020_CSV_URL = "https://cordis.europa.eu/data/cordis-h2020projects-csv.zip"
HE_CSV_URL = "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip"
DATASETS: tuple[tuple[str, str], ...] = (("h2020", H2020_CSV_URL), ("he", HE_CSV_URL))

# Locked normalized subset schema (decisions.md 2026-09-28 "CORDIS snapshot
# + normalized-schema contract") — order and names are the stable interface
# build_tables.py reads.
NORMALIZED_COLUMNS = (
    "title",
    "objective",
    "eu_contribution",
    "project_id",
    "status",
    "start_date",
    "end_date",
    "programme",
)
# The four columns the match rule / report payload cannot work without; a
# distribution missing one degrades the dataset loudly (AC7/AC8).
REQUIRED_COLUMNS = ("title", "objective", "eu_contribution", "project_id")

# Case-insensitive, whitespace-stripped header alias sets -> normalized
# columns. Finalized against the LIVE header row of both distributions
# (identical 22-column layout, specs/issue-7-extdocs.md):
#   id;acronym;status;title;startDate;endDate;totalCost;ecMaxContribution;
#   topics;ecSignatureDate;frameworkProgramme;masterCall;subCall;
#   fundingScheme;nature;objective;contentUpdateDate;rcn;grantDoi;keywords;
#   Human-validated;legalBasis
# Extra candidates cover the documented alternate spellings so a future
# monthly regeneration that renames a header still maps (or raises loudly).
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "projecttitle"),
    "objective": ("objective", "projectobjective"),
    "eu_contribution": ("ecmaxcontribution", "eucontribution", "eucost", "communitycontribution"),
    "project_id": ("id", "projectid", "cordisprojectid"),
    "status": ("status", "projectstatus", "projectstatusvalue"),
    "start_date": ("startdate",),
    "end_date": ("enddate",),
    "programme": ("frameworkprogramme", "programme", "programmeoractivity"),
}

# ZIP member carrying the project table in both live distributions.
PROJECT_MEMBER = "project.csv"

# Relevance sampling (issue-53 WI-02 / D-03, AC-4): per package × keyword
# list, up to N matched project titles are LOGGED so the operator can verify
# the list measures what the report's claim asserts. stdout only — snapshots,
# manifests and the stem-pair file contract are untouched. Cut fallback: 10.
SAMPLE_SIZE = 20


def load_cordis_queries(config_path: Path = CONFIG_PATH) -> dict[str, list[str]]:
    """Per package, the queries.yaml cordis strings verbatim (FR-1/AC2).

    Raises KeyError on a missing cordis section and ValueError if any package
    list is empty (queries.yaml contract guarantees non-empty cells —
    decisions.md 2026-09-26 "queries.yaml content contract"; mirrors the guard
    in pull_ctgov.load_ctgov_queries). Strings are comma-separated lowercase
    keyword lists matched locally (D-4) — passed through verbatim, never
    re-joined or truncated.
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    section = cfg.get("cordis") if isinstance(cfg, dict) else None
    if not section:
        raise KeyError(f"missing 'cordis' section in {config_path}")
    queries: dict[str, list[str]] = {}
    for package, strings in section.items():
        if not strings:
            raise ValueError(f"package {package!r} has no cordis query in {config_path}")
        queries[package] = list(strings)
    return queries


def download_csv(session: _common.ResilientClient, url: str) -> Path:
    """Stream one distribution body to a NamedTemporaryFile(delete=False);
    return its Path — the caller removes it in a finally (AC5).

    httpx 0.28.1 removed the stream= kwarg from Client.get/request, so the
    spec's original `ResilientClient.get(url, stream=True)` sketch raises
    TypeError (specs/issue-7-extdocs.md CRITICAL finding; _common.py is
    frozen by AC11). Streaming therefore goes through the inner client's
    stream() context manager (forwarded via ResilientClient.__getattr__),
    which bypasses ResilientClient.request's retry loop — to keep AC5's
    inherited retry/backoff, this loop reuses _common's retry POLICY
    verbatim (MAX_RETRIES, _is_retryable_status, _retry_after_delay,
    _backoff_delay) around the streamed GET (decisions.md 2026-09-28
    issue-7 streaming route). The body is written chunk-by-chunk and never
    buffered whole in RAM; the context manager closes the streamed response
    on every path; a non-200 after retries raises the path-only RuntimeError
    idiom (pull_ctgov.py:92); on any failure the temp file is removed here,
    so the caller owns it only on success.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", prefix="cordis-")
    tmp_path = Path(tmp.name)
    ok = False
    try:
        for attempt in range(_common.MAX_RETRIES + 1):
            try:
                with session.stream("GET", url) as response:
                    if response.status_code != 200:
                        if _common._is_retryable_status(response.status_code) and (
                            attempt < _common.MAX_RETRIES
                        ):
                            delay = _common._retry_after_delay(response)
                            if delay is None:
                                delay = _common._backoff_delay(attempt)
                            time.sleep(delay)
                            continue
                        # Name path only (no query string) — credential-free
                        # message idiom from pull_ctgov.py:92.
                        raise RuntimeError(
                            f"cordis {response.status_code} for {response.url.path}"
                        )
                    for chunk in response.iter_bytes():
                        tmp.write(chunk)
                ok = True
                return tmp_path
            except httpx.TransportError:
                if attempt == _common.MAX_RETRIES:
                    raise
                # Partial bytes from the aborted stream must not pollute the retry.
                tmp.seek(0)
                tmp.truncate()
                time.sleep(_common._backoff_delay(attempt))
        raise AssertionError("unreachable: download loop always returns or raises")
    finally:
        tmp.close()
        if not ok:
            tmp_path.unlink(missing_ok=True)


# --- Live-shape repair (specs/issue-7-extdocs.md, verified 2026-09-28) ------
#
# Both distributions' project.csv carry ~0.8% records whose free-text fields
# (objective, sometimes keywords) contain doubled/unescaped quotes that break
# naive CSV quoting: the lenient csv module (strict=False) splits them on
# ';' INSIDE the text, producing rows with more than the 22 columns. The
# pandas python engine drops those rows silently (its strict csv reader
# raises ';' expected after '"' and on_bad_lines never fires), and the C
# engine aborts the whole file with ParserError — so the rows are repaired
# before pd.read_csv, never skipped (raw fidelity: no silent drops).
# Repair anchors on the fixed live layout (identical header in both files):
#   cols 0..14 stable prefix (id..nature), 15 objective, 16 contentUpdateDate
#   (the only datetime column), 17 rcn, 18 grantDoi, 19 keywords,
#   20 Human-validated, 21 legalBasis. Fragments between the objective start
#   and the datetime anchor are rejoined with ';'; fragments after the
#   grantDoi (keywords) likewise. A row that cannot be anchored raises — the
#   dataset then degrades per the AC8 fetch boundary, never silently.
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _repair_row(row: list[str], ncols: int) -> list[str]:
    """Rejoin a quote-fragmented row to exactly ncols fields or raise.

    Layout assumption (live-verified, both distributions identical): the
    objective is 7 columns from the end and contentUpdateDate — the only
    datetime column — 6 from the end; only those two regions (objective,
    keywords) ever fragment in practice.
    """
    if len(row) < ncols:
        raise ValueError(f"cordis row {row[0]!r} has {len(row)} fields < {ncols} (unmergeable)")
    obj = ncols - 7
    dt = None
    for i in range(len(row) - 6, obj - 1, -1):
        if _DATETIME_RE.match(row[i]):
            dt = i
            break
    if dt is None:
        raise ValueError(f"cordis row {row[0]!r} has {len(row)} fields but no datetime anchor")
    # Head-anchor sanity check: startDate/endDate (cols 4/5 in the fixed live
    # layout) must still be intact. If quote corruption fragmented any column
    # before the objective, the datetime anchor is found shifted right and the
    # reconstruction below would silently misalign every column after the
    # break point — raise instead so the row degrades loudly via pull_all.
    if not (_DATE_RE.match(row[4]) and _DATE_RE.match(row[5])):
        raise ValueError(f"cordis row {row[0]!r} fragments outside objective/keywords")
    # objective = fragments up to the anchor; tail = anchor, rcn, doi,
    # keywords (rejoined), Human-validated, legalBasis.
    return (
        row[:obj]
        + [";".join(row[obj:dt]), row[dt], row[dt + 1], row[dt + 2]]
        + [";".join(row[dt + 3 : -2]), row[-2], row[-1]]
    )


@contextmanager
def _open_body(path: Path):
    """Yield a seekable binary stream over the CSV body of one distribution.

    Live distributions are application/zip archives (magic PK\\x03\\x04) whose
    project table is the project.csv member; a plain CSV body is accepted
    too. An HTML body raises — the data.europa.eu SPA answers 200 on any
    path (spec Risk 1), so the content check happens here, at pull time,
    before any parser sees markup.
    """
    with ExitStack() as stack:
        raw = stack.enter_context(open(path, "rb"))
        magic = raw.read(4)
        raw.seek(0)
        if magic[:2] == b"PK":
            archive = stack.enter_context(zipfile.ZipFile(path))
            if PROJECT_MEMBER not in archive.namelist():
                raise ValueError(
                    f"cordis archive has no {PROJECT_MEMBER!r} member: {archive.namelist()}"
                )
            body = stack.enter_context(archive.open(PROJECT_MEMBER))
        else:
            body = raw
        head = body.read(4096)
        if head.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            raise ValueError(
                "cordis distribution body is HTML (SPA shell?), not CSV/ZIP — "
                "re-verify the distribution URL in a browser (queries.yaml caveat)"
            )
        body.seek(0)
        yield body


def _probe_encoding(sample: bytes) -> str:
    """utf-8-sig on BOM, else utf-8 when the sample decodes, else latin-1.

    pandas does not auto-detect encoding (extdocs pandas block); a sample cut
    mid-multibyte-character is still utf-8 (the error sits in the last 3
    bytes), and latin-1 is the safe fallback that decodes any byte.
    """
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start >= len(sample) - 3:
            return "utf-8"
        return "latin-1"
    return "utf-8"


def _probe_delimiter(sample: str) -> str:
    """csv.Sniffer on the first KB restricted to ",", ";" — but the Sniffer
    tie-breaks toward ',' no matter the counts (extdocs csv-block gotcha), so
    a header-line majority of the other candidate overrides it; the count
    fallback also covers sniff() raising csv.Error on a uniform sample."""
    header = sample.split("\n", 1)[0].split("\r", 1)[0]
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters=",;").delimiter
    except csv.Error:
        sniffed = None
    counts = {",": header.count(","), ";": header.count(";")}
    if counts[";"] > counts[","]:
        return ";"
    if counts[","] > counts[";"]:
        return ","
    return sniffed if sniffed in (",", ";") else ","


def read_and_normalize(path: Path) -> pd.DataFrame:
    """Probe → repair → pd.read_csv from disk → alias-map to the 8-column subset.

    Delimiter/encoding are probed per distribution (AC7 — they may differ;
    live they are identical: ';' + UTF-8). Rows go through the anchor-repair
    pass (lenient csv module, strict=False — the only lossless tokenizer for
    this data, see the repair note above) and are written back as a clean
    QUOTE_ALL CSV to a temp file on disk, which pd.read_csv then parses with
    dtype=str, keep_default_na=False — raw fidelity: header normalization
    only, no value coercion (EUR strings with decimal commas and dates pass
    through as written). The repaired temp is removed in a finally. Headers
    map to NORMALIZED_COLUMNS via case-insensitive, whitespace-stripped
    HEADER_ALIASES; a missing REQUIRED alias raises (the dataset degrades in
    pull_all's fetch try — never a silent partial schema); optional columns
    absent on one distribution become empty columns (edge case, spec).
    """
    with _open_body(path) as body:
        sample = body.read(4096)
        encoding = _probe_encoding(sample)
        delimiter = _probe_delimiter(sample.decode(encoding, errors="replace"))
        body.seek(0)
        reader = csv.reader(
            io.TextIOWrapper(body, encoding=encoding, newline=""), delimiter=delimiter
        )
        header = next(reader)
        ncols = len(header)
        repaired_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(suffix=".csv", prefix="cordis-repaired-")
            os.close(fd)
            repaired_path = Path(name)
            with open(repaired_path, "w", encoding="utf-8", newline="") as out:
                writer = csv.writer(
                    out, delimiter=delimiter, quoting=csv.QUOTE_ALL, lineterminator="\n"
                )
                writer.writerow(header)
                for row in reader:
                    if not row:
                        continue
                    writer.writerow(row if len(row) == ncols else _repair_row(row, ncols))
            # Repaired file is UTF-8 by construction; the probed delimiter carries over.
            df = pd.read_csv(repaired_path, sep=delimiter, dtype=str, keep_default_na=False)
        finally:
            if repaired_path is not None:
                repaired_path.unlink(missing_ok=True)
    return _normalize_columns(df)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map live headers to the locked 8-column subset, order fixed (AC7)."""
    norm = {re.sub(r"\s+", "", str(c)).lower(): c for c in df.columns}
    mapped: dict[str, pd.Series] = {}
    for col in NORMALIZED_COLUMNS:
        source = next((norm[a] for a in HEADER_ALIASES[col] if a in norm), None)
        if source is not None:
            mapped[col] = df[source]
        elif col in REQUIRED_COLUMNS:
            raise ValueError(
                f"cordis distribution missing required column {col!r}; header was {list(df.columns)}"
            )
        else:
            # map-if-present columns absent on this distribution: empty column kept.
            mapped[col] = pd.Series("", index=df.index)
    return pd.DataFrame(mapped, columns=list(NORMALIZED_COLUMNS))


def filter_by_keywords(df: pd.DataFrame, query_strings: list[str]) -> pd.DataFrame:
    """D-4 match rule (AC2): ANY keyword, case-insensitive substring, in
    title OR objective.

    Keywords are the comma-separated tokens of the package's verbatim
    queries.yaml strings (FR-1 grammar, decisions.md 2026-09-26), lowercased
    and deduped (duplicate tokens across a package's strings cannot
    double-count rows). Each token is re.escape'd before the '|' join —
    literal patterns under regex=True misfire (extdocs pandas gotcha: e.g.
    "c++" or "multi-omics" must stay literal). keep_default_na=False means
    cells are plain str, so na=False is belt-and-braces, never a NaN path.
    Zero matches -> empty frame; the caller still writes a header-only
    snapshot (AC4). A project may match several packages — no dedup here
    (FR-6, overlap allowed).
    """
    keywords = {
        k.strip().lower() for s in query_strings for k in s.split(",") if k.strip()
    }
    if not keywords:
        return df.iloc[0:0]
    pattern = "|".join(re.escape(k) for k in sorted(keywords))
    mask = df["title"].str.contains(pattern, case=False, regex=True, na=False) | df[
        "objective"
    ].str.contains(pattern, case=False, regex=True, na=False)
    return df[mask]


def pull_all(
    session: _common.ResilientClient,
    *,
    base_dir: Path | str | None = None,
    config_path: Path = CONFIG_PATH,
) -> tuple[list[Path], list[str]]:
    """Per dataset: fetch+normalize, then one snapshot+manifest per package.

    The testable seam: base_dir=None uses write_snapshot's production default
    (real data/raw/); gates pass a temp dir. The try/except wraps ONLY the
    fetch+normalize phase (decisions.md 2026-09-28 failure boundary): a
    download/probe/repair failure warns, records the dataset key and
    continues to the next dataset. The per-package write_snapshot loop runs
    OUTSIDE the try — a same-day collision (FileExistsError) or TypeError
    aborts loudly, never degrades (AC8). The temp CSV is removed in the
    dataset's finally even when the write loop aborts. Manifest `query` is
    the package's verbatim string list (decisions.md 2026-09-28 multi-string
    ruling); `url` is the direct download URL (keyless — _redact_url no-op).
    A project may match several packages — no dedup across packages (FR-6).
    Returns (written paths, failed dataset keys) — mirrors pull_ctgov shape.
    """
    paths: list[Path] = []
    failures: list[str] = []
    queries = load_cordis_queries(config_path)
    for key, url in DATASETS:
        tmp: Path | None = None
        try:  # fetch + normalize phase only — see decisions.md 2026-09-28.
            tmp = download_csv(session, url)
            df = read_and_normalize(tmp)
        except Exception as exc:  # noqa: BLE001 — dataset boundary degrades (AC8/AC9)
            logging.warning("cordis dataset %r failed: %s", key, exc)
            failures.append(key)
            continue
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        # OUTSIDE the try: a same-day collision must abort, never degrade (AC8).
        for package, strings in queries.items():
            for sidx, s in enumerate(strings):  # WI-02 per-query relevance sample
                per_query = filter_by_keywords(df, [s])
                logging.info("cordis relevance sample — package %r keyword-list #%d (%d hits):",
                             package, sidx + 1, len(per_query))
                for rec in per_query.head(SAMPLE_SIZE).itertuples(index=False):
                    logging.info("  %s", str(rec.title)[:120])
            paths.append(
                _common.write_snapshot(
                    filter_by_keywords(df, strings),
                    "cordis",
                    strings,
                    url=url,
                    name=f"{package}-{key}",
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

    No API key and no .env required (AC11): get_session() omits the
    User-Agent header gracefully when CONTACT_EMAIL is unset. Exit code: 0
    if every dataset succeeded, 1 if any failed (AC9). A same-day collision
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
        logging.warning("%d cordis dataset(s) failed: %s", len(failures), ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
