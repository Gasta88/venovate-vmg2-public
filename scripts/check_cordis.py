"""Ad-hoc verification gates for scripts/pull_cordis.py (issue-7).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_cordis.py

Prints `G-<n> PASS|FAIL` per gate (G-1…G-8 + G-INT) and exits non-zero if any
gate fails. Not part of the runtime pipeline. No gate touches the network or
real data/raw/ — httpx.MockTransport + temp dirs throughout (same pattern as
scripts/check_ctgov.py). Gate fixtures emit the LIVE-VERIFIED distribution
shape (specs/issue-7-extdocs.md): application/zip archives whose project.csv
member is `;`-delimited UTF-8 with the 22-column CORDIS header, including the
doubled-quote free-text rows that the anchor-repair pass must rejoin.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import pull_cordis  # noqa: E402

# Fixture identity — gates assert this email never appears in any manifest
# (G-INT credential check) and that the pull adds no auth of its own (AC11).
FIXTURE_EMAIL = "gate@example.invalid"

# The live 22-column CORDIS project.csv header (both distributions, identical
# — specs/issue-7-extdocs.md). Repair anchors depend on this layout:
# objective at index 15, contentUpdateDate (datetime) at 16, tail 6 columns.
LIVE_HEADER = ";".join(
    [
        "id", "acronym", "status", "title", "startDate", "endDate", "totalCost",
        "ecMaxContribution", "topics", "ecSignatureDate", "frameworkProgramme",
        "masterCall", "subCall", "fundingScheme", "nature", "objective",
        "contentUpdateDate", "rcn", "grantDoi", "keywords", "Human-validated",
        "legalBasis",
    ]
)


def project_row(
    pid: str,
    title: str,
    objective: str,
    contribution: str = "100000",
    status: str = "CLOSED",
    start: str = "2020-01-01",
    end: str = "2023-12-31",
    programme: str = "H2020",
) -> str:
    """One fully-quoted live-shaped project.csv row (all 22 fields)."""
    fields = [
        pid, "ACR", status, title, start, end, contribution, contribution,
        "CALL-TOPIC", "2019-10-28", programme, "CALL", "CALL", "RIA", "",
        objective, "2026-08-06 12:00:00", "99999", f"10.3030/{pid}",
        "kw1 kw2", "false", "H2020-EU.1.1.",
    ]
    return ";".join('"' + f.replace('"', '""') + '"' for f in fields)


def make_project_zip(csv_text: str) -> bytes:
    """application/zip fixture mirroring the live distribution layout."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(pull_cordis.PROJECT_MEMBER, csv_text)
    return buf.getvalue()


# 2 packages x keyword-list strings mirroring the queries.yaml grammar
# (comma-separated lowercase, D-4). pkgone: 2 strings, pkgtwo: 1.
PKGONE_STRINGS = ["venom, peptide, toxin", "snakebite, antvenom"]
PKGTWO_STRINGS = ["multi-omics, proteomics"]
FIXTURE_TWO_PKG = (
    "cordis:\n"
    f"  pkgone:\n    - '{PKGONE_STRINGS[0]}'\n    - '{PKGONE_STRINGS[1]}'\n"
    f"  pkgtwo:\n    - '{PKGTWO_STRINGS[0]}'\n"
)

# Fixture CSV: rows engineered for the match rule — peptide in title, venom
# only in objective, keyword only in a non-match field (programme), zero
# match, and one doubled-quote objective that fragments under naive CSV
# quoting (the live ~0.8% repair case, specs/issue-7-extdocs.md — the raw
# row below mirrors the live record 633263 pattern byte-for-byte in shape:
# field opens with a doubled quote, contains ';', and carries "" escapes).
FRAGMENTED_ROW = (
    '"100005";"TOXFIX";"CLOSED";"Toxin neutralisation study";"2020-01-01";"2023-12-31";'
    '"50000";"50000";"TOPIC";"2019-10-28";"H2020";"CALL";"CALL";"RIA";"";'
    '""We study snake venom peptides; the Ministry called it a ""great success"" '
    'for drug discovery.";"2026-08-06 12:00:00";"99999";"10.3030/100005";'
    '"venom peptides";"false";"H2020-EU.1.1."'
)
FIXTURE_PROJECT_CSV = "\n".join(
    [
        LIVE_HEADER,
        project_row("100001", "Novel peptide synthesis platform", "No match text."),
        project_row("100002", "Marine resources", "Venom derived conotoxin analysis."),
        project_row("100003", "Project", "Objective text.", programme="H2020-SNAKEBITE"),
        project_row("100004", "Astronomy and galaxies", "Nothing relevant here."),
        FRAGMENTED_ROW,
        "",
    ]
)


def fake_session(handler):
    """ResilientClient over httpx.MockTransport — no network (check_common pattern)."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return _common.ResilientClient(client)


def write_fixture(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(body, encoding="utf-8")
    return path


def date_dir(tmp_raw: Path, source: str = "cordis") -> Path:
    dirs = list((tmp_raw / source).iterdir())
    assert len(dirs) == 1, dirs
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", dirs[0].name), dirs[0].name
    return dirs[0]


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def dir_bytes(root: Path) -> dict[str, bytes]:
    # root/<source>/<date>/<file> — three levels below the base_dir.
    return {str(p.relative_to(root)): p.read_bytes() for p in root.glob("*/*/*")}


def manifest_of(snapshot: Path) -> dict:
    return json.loads((snapshot.parent / f"{snapshot.stem}.manifest.json").read_text("utf-8"))


class _TrackedStream(httpx.SyncByteStream):
    """MockTransport body that records closure + yields in chunks."""

    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def g3_download_stream(tmp: Path) -> None:
    """Streaming download: bytes land in temp file, response closed, non-200
    → path-only RuntimeError, 503-then-200 succeeds in 2 attempts (G-3)."""
    served = make_project_zip(FIXTURE_PROJECT_CSV)
    url = "https://cordis.test/data/a.csv"

    # Happy path: chunked streamed body lands byte-identical; response closed.
    stream = _TrackedStream([served[:1000], served[1000:2000], served[2000:]])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    temps_before = set(p.name for p in Path(tempfile.gettempdir()).glob("cordis-*"))
    with fake_session(handler) as session:
        path = pull_cordis.download_csv(session, url)
    try:
        assert path.read_bytes() == served, "streamed temp must equal served bytes"
        assert stream.closed, "streamed response must be closed after download"
        assert path.name.startswith("cordis-") and path.suffix == ".csv", path
    finally:
        path.unlink(missing_ok=True)
    assert (
        set(p.name for p in Path(tempfile.gettempdir()).glob("cordis-*")) == temps_before
    ), "success path leaves no stray temps"

    # Non-200 (400, non-retryable): RuntimeError naming status + URL path only —
    # never the query string (credential-free message idiom, pull_ctgov:92).
    def always_400(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"nope")

    with fake_session(always_400) as session:
        try:
            pull_cordis.download_csv(session, "https://cordis.test/data/a.csv?api_key=SECRET")
        except RuntimeError as exc:
            msg = str(exc)
            assert "400" in msg and "/data/a.csv" in msg, msg
            assert "api_key" not in msg and "SECRET" not in msg, msg
        else:
            raise AssertionError("non-200 must raise RuntimeError")
    # Failed download removes its own temp — none of the attempts leak.
    assert (
        set(p.name for p in Path(tempfile.gettempdir()).glob("cordis-*")) == temps_before
    ), "failure path must clean up its temp file"

    # 503-then-200: retry/backoff policy inherited from _common succeeds in
    # exactly two attempts (AC5; decisions.md 2026-09-26 policy, reused via
    # _common.MAX_RETRIES/_is_retryable_status/_backoff_delay — see download_csv
    # docstring for the httpx 0.28 streaming-route note).
    attempts = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, content=b"unavailable")
        return httpx.Response(200, content=served)

    with fake_session(flaky) as session:
        path = pull_cordis.download_csv(session, url)
    try:
        assert attempts["n"] == 2, attempts
        assert path.read_bytes() == served
    finally:
        path.unlink(missing_ok=True)


def g4_probe_normalize_repair(tmp: Path) -> None:
    """Delimiter/encoding probes, alias mapping to the 8-col subset (order
    locked), live-shape repair, and loud raises (G-4, AC7)."""

    def body_file(name: str, data: bytes) -> Path:
        p = tmp / name
        p.write_bytes(data)
        return p

    # (a) ';' + UTF-8-BOM, aliased header spellings -> normalized subset.
    bom_csv = ("\ufeff" + "\n".join(
        [
            "Project Title;Project Objective;EU Contribution;CORDIS Project ID;"
            "Project Status;Start Date;End Date;Programme",
            '"Peptide platform";"obj text";"2073781,25";"123";"CLOSED";'
            '"2020-01-01";"2023-12-31";"H2020"',
            "",
        ]
    )).encode("utf-8")
    df = pull_cordis.read_and_normalize(body_file("bom.csv", bom_csv))
    assert list(df.columns) == list(pull_cordis.NORMALIZED_COLUMNS), df.columns
    assert df.iloc[0]["eu_contribution"] == "2073781,25", df.iloc[0]  # raw fidelity: str
    assert df.iloc[0]["title"] == "Peptide platform" and df.iloc[0]["project_id"] == "123"

    # (b) ',' + latin-1 (é bytes), live-style aliases, extra columns dropped.
    lat = (
        "id,title,objective,ecMaxContribution,status,startDate,endDate,"
        "frameworkProgramme,coordinatorCountry\n"
        '"1","Venom study","obj\xe9t\xe9","1000","OPEN","2021-01-01","2027-12-31","HORIZON","FR"\n'
    ).encode("latin-1")
    df = pull_cordis.read_and_normalize(body_file("lat.csv", lat))
    assert list(df.columns) == list(pull_cordis.NORMALIZED_COLUMNS), df.columns
    assert df.iloc[0]["objective"] == "objété", df.iloc[0]["objective"]

    # (c) ZIP body (live layout) incl. the doubled-quote fragmented row: the
    # repair pass rejoins objective fragments split on ';' (live ~0.8%).
    df = pull_cordis.read_and_normalize(body_file("dist.zip", make_project_zip(FIXTURE_PROJECT_CSV)))
    assert df.shape[1] == 8 and len(df) == 5, df.shape
    frag = df[df["project_id"] == "100005"].iloc[0]
    assert "snake venom peptides" in frag["objective"] and "great success" in frag["objective"], frag
    assert ";" in frag["objective"], "fragments must be rejoined with the delimiter"
    assert frag["eu_contribution"] == "50000" and frag["status"] == "CLOSED", frag
    # plain-CSV (non-zip) bodies parse identically (accepted both shapes).
    df2 = pull_cordis.read_and_normalize(body_file("plain.csv", FIXTURE_PROJECT_CSV.encode("utf-8")))
    assert df2.equals(df), (df2.shape, df.shape)

    # (d) missing REQUIRED alias -> raise (degrades via pull_all fetch try).
    noobj = 'id,title,ecMaxContribution,frameworkProgramme\n"1","t","5","H2020"\n'
    try:
        pull_cordis.read_and_normalize(body_file("noobj.csv", noobj.encode("utf-8")))
    except ValueError as exc:
        assert "objective" in str(exc), exc
    else:
        raise AssertionError("missing required alias must raise")

    # (e) optional columns absent -> empty columns kept, snapshot still valid.
    req_only = 'id,title,objective,ecMaxContribution\n"1","t","o","5"\n'
    df = pull_cordis.read_and_normalize(body_file("req.csv", req_only.encode("utf-8")))
    assert list(df.columns) == list(pull_cordis.NORMALIZED_COLUMNS)
    assert all((df[c] == "").all() for c in ("status", "start_date", "end_date", "programme")), df

    # (f) HTML body (SPA shell answering 200 on any path — Risk 1) -> raise.
    try:
        pull_cordis.read_and_normalize(
            body_file("shell.html", b"<!DOCTYPE html><html><head><title>European Data Portal")
        )
    except ValueError as exc:
        assert "HTML" in str(exc), exc
    else:
        raise AssertionError("HTML body must raise, never feed markup to the parser")

    # (g) zip without the project member -> raise.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("organization.csv", "a;b\n1;2\n")
    try:
        pull_cordis.read_and_normalize(body_file("wrong.zip", buf.getvalue()))
    except ValueError as exc:
        assert "project.csv" in str(exc), exc
    else:
        raise AssertionError("archive without project.csv member must raise")


def g2_match_rule(tmp: Path) -> None:
    """ANY-keyword case-insensitive substring in title OR objective; match
    fields only; overlap across packages; regex metachars literal; empty
    cells; duplicate keywords (G-2, AC2/FR-6, D-4)."""
    df = pd.DataFrame(
        {
            "title": [
                "Venom Peptide Platform", "Marine Toxins", "SnakeBite response",
                "Project", "C++ tools", "", "Venom; peptides etc",
            ],
            "objective": [
                "no match", "Conotoxin analysis", "no match",
                "A multi-omics pipeline", "no match", "", "no match",
            ],
            "eu_contribution": ["1", "2", "3", "4", "5", "6", "7"],
            "project_id": ["1", "2", "3", "4", "5", "6", "7"],
            "status": ["CLOSED"] * 7,
            "start_date": ["2020-01-01"] * 7,
            "end_date": ["2023-12-31"] * 7,
            # snakebite/horizon live ONLY here — must never match (match fields
            # are title/objective per D-4).
            "programme": ["SNAKEBITE horizon", "H2020", "H2020", "HORIZON", "HORIZON", "H2020", "H2020"],
        }
    )
    hits = pull_cordis.filter_by_keywords(df, ["venom, peptide, toxin, snake", "conotoxin, snakebite"])
    assert list(hits["project_id"]) == ["1", "2", "3", "7"], list(hits["project_id"])
    # Keyword present only in a non-match field -> excluded (programme ignored;
    # "horizon" occurs in programme rows 1/4/5 but never in title/objective).
    assert pull_cordis.filter_by_keywords(df, ["horizon"]).empty
    # Overlap: one project may match several packages — both filters keep it (FR-6).
    a = pull_cordis.filter_by_keywords(df, ["venom"])
    b = pull_cordis.filter_by_keywords(df, ["peptide"])
    assert "1" in set(a["project_id"]) and "1" in set(b["project_id"]), (a["project_id"], b["project_id"])
    # Regex metacharacters stay literal (re.escape): hyphen + 'c++'.
    assert list(pull_cordis.filter_by_keywords(df, ["multi-omics"])["project_id"]) == ["4"]
    assert list(pull_cordis.filter_by_keywords(df, ["c++"])["project_id"]) == ["5"]
    # Empty title/objective cells never match and never crash.
    assert "6" not in set(pull_cordis.filter_by_keywords(df, ["venom, peptide"])["project_id"])
    # Duplicate keywords across a package's strings -> deduped, no double rows.
    dup = pull_cordis.filter_by_keywords(df, ["venom, toxin", "venom"])
    assert list(dup["project_id"]) == ["1", "2", "7"], list(dup["project_id"])
    # Degenerate cell: string of only separators -> no keywords -> empty frame, no crash.
    assert pull_cordis.filter_by_keywords(df, [", ,  ,"]).empty


def zip_handler(h2020_body: bytes, he_body: bytes):
    """MockTransport routing the two REAL module-constant URLs to fixture bytes."""

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == pull_cordis.H2020_CSV_URL:
            return httpx.Response(200, content=h2020_body)
        if str(req.url) == pull_cordis.HE_CSV_URL:
            return httpx.Response(200, content=he_body)
        return httpx.Response(404, content=b"unexpected url")

    return handler


def fixture_zips() -> tuple[bytes, bytes]:
    return make_project_zip(FIXTURE_PROJECT_CSV), make_project_zip(FIXTURE_PROJECT_CSV)


def g5_snapshots_manifests(tmp: Path) -> None:
    """12 stem-pairs under cordis/<date>/ + full manifest contract (G-5, AC3/5)."""
    h2020, he = fixture_zips()
    cfg = pull_cordis.CONFIG_PATH  # REAL config: 6 packages x 2 datasets = 12 pairs
    with fake_session(zip_handler(h2020, he)) as session:
        paths, failures = pull_cordis.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert failures == [], failures
    d = date_dir(tmp / "raw")
    real_queries = pull_cordis.load_cordis_queries(cfg)
    stems = {f"{package}-{dataset}" for package in real_queries for dataset in ("h2020", "he")}
    names = sorted(p.name for p in d.iterdir())
    assert names == sorted(
        [f"{s}.csv" for s in stems] + [f"{s}.manifest.json" for s in stems]
    ), names  # exactly 12 stem-pairs = 24 files, stems <package>-h2020/-he
    assert len(paths) == 12 and all(p.parent == d for p in paths), paths
    for package, strings in real_queries.items():
        for dataset in ("h2020", "he"):
            snap = d / f"{package}-{dataset}.csv"
            manifest = manifest_of(snap)
            assert manifest["source"] == "cordis", manifest
            assert manifest["format"] == "csv", manifest
            assert manifest["query"] == strings, (package, manifest["query"])  # verbatim LIST (Decision A)
            url = pull_cordis.H2020_CSV_URL if dataset == "h2020" else pull_cordis.HE_CSV_URL
            assert manifest["url"] == _common._redact_url(url) == url, manifest["url"]
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest["timestamp"]), manifest
            assert manifest["snapshot"] == snap.name, manifest
            raw = snap.read_bytes()
            assert manifest["sha256"] == hashlib.sha256(raw).hexdigest(), manifest  # re-derivable
            rows = pd.read_csv(snap)
            assert manifest["record_count"] == len(rows), (manifest, len(rows))
            assert list(rows.columns) == list(pull_cordis.NORMALIZED_COLUMNS), rows.columns
    # Spot-check the D-4 match against the fixture CSV: p5_venomate's retuned
    # lists (issue-53 WI-02 — bare `peptide` dropped) hit the venom/toxin and
    # repaired-fragment rows; p2_insilico_workshop yields zero.
    assert manifest_of(d / "p5_venomate-h2020.csv")["record_count"] == 2, "p5_venomate fixture hits"
    assert manifest_of(d / "p2_insilico_workshop-he.csv")["record_count"] == 0, "zero-yield visible"


def g6_zero_match(tmp: Path) -> None:
    """Zero-match package -> header-only CSV, record_count 0, sha256 of header bytes (G-6, AC4)."""
    cfg = write_fixture(tmp, "zero.yaml", "cordis:\n  nothinx:\n    - 'zzz, qqqq'\n")
    h2020, he = fixture_zips()
    with fake_session(zip_handler(h2020, he)) as session:
        paths, failures = pull_cordis.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert failures == [] and len(paths) == 2, (failures, paths)
    d = date_dir(tmp / "raw")
    header_only = ",".join(pull_cordis.NORMALIZED_COLUMNS) + "\n"
    for snap in (d / "nothinx-h2020.csv", d / "nothinx-he.csv"):
        assert snap.read_text("utf-8") == header_only, snap  # header written, no rows
        manifest = manifest_of(snap)
        assert manifest["record_count"] == 0, manifest
        assert manifest["sha256"] == hashlib.sha256(snap.read_bytes()).hexdigest(), manifest
        assert list(pd.read_csv(snap).columns) == list(pull_cordis.NORMALIZED_COLUMNS)


def g7_same_day_collision(tmp: Path) -> None:
    """Second pull_all into same base_dir: FileExistsError propagates through
    main(), bytes unchanged, NOT swallowed into failures (G-7, AC8)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    h2020, he = fixture_zips()

    with fake_session(zip_handler(h2020, he)) as session:
        pull_cordis.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
        before = dir_bytes(tmp / "raw")
        assert len(before) == 8, before  # 2 packages x 2 datasets x (csv + manifest)
        try:
            pull_cordis.main(session=session, base_dir=tmp / "raw", config_path=cfg)
        except FileExistsError:
            pass
        else:
            raise AssertionError("same-day re-pull must raise FileExistsError through main (AC8)")
        assert dir_bytes(tmp / "raw") == before  # no overwrite, no manifest rewrite


def g8_failure_degrades(tmp: Path) -> None:
    """One dataset 400 -> warn names key, other dataset's snapshots present,
    failures==[key], main()==1; all-ok ==0; injected session survives (G-8)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    h2020, he = fixture_zips()

    def one_down(req: httpx.Request) -> httpx.Response:
        if str(req.url) == pull_cordis.H2020_CSV_URL:
            return httpx.Response(400, content=b"bad request")  # non-retryable -> fast
        return httpx.Response(200, content=he)

    capture = _LogCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    try:
        with fake_session(one_down) as session:
            paths, failures = pull_cordis.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    finally:
        root.removeHandler(capture)
    assert failures == ["h2020"], failures
    assert [p.name for p in paths] == ["pkgone-he.csv", "pkgtwo-he.csv"], paths
    d = date_dir(tmp / "raw")
    assert not (d / "pkgone-h2020.csv").exists(), "failed dataset must leave no partial snapshot"
    warnings = [r.getMessage() for r in capture.records if r.levelno == logging.WARNING]
    assert any("h2020" in m for m in warnings), warnings  # logging.warning names the dataset key

    # main() exit-code contract via the in-process DI seam (no network).
    with fake_session(one_down) as session:
        rc = pull_cordis.main(session=session, base_dir=tmp / "raw2", config_path=cfg)
    assert rc == 1, rc  # AC9: any dataset failed
    with fake_session(zip_handler(h2020, he)) as session:
        rc = pull_cordis.main(session=session, base_dir=tmp / "raw3", config_path=cfg)
    assert rc == 0, rc  # all-success
    # Injected sessions are the caller's: main() must not close them.
    with fake_session(zip_handler(h2020, he)) as session:
        assert pull_cordis.main(session=session, base_dir=tmp / "raw4", config_path=cfg) == 0
        assert len(pull_cordis.pull_all(session, base_dir=tmp / "raw5", config_path=cfg)[0]) == 4


def g_int_real_config(tmp: Path) -> None:
    """REAL queries.yaml + fixture CSVs for both URLs in-process: 24 files,
    manifests credential/PII-free, temps cleaned even on the collision-abort
    path (G-INT; no network, no real data/raw/)."""
    h2020, he = fixture_zips()
    cfg = pull_cordis.CONFIG_PATH
    temps_before = set(Path(tempfile.gettempdir()).glob("cordis-*"))
    with fake_session(zip_handler(h2020, he)) as session:
        paths, failures = pull_cordis.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert failures == [] and len(paths) == 12, (failures, len(paths))
    d = date_dir(tmp / "raw")
    files = sorted(p.name for p in d.iterdir())
    assert len(files) == 24, files  # 12 stem-pairs from the REAL 6-package config
    # No credential/PII substrings in ANY manifest (AC11 keyless; _redact_url
    # has nothing to redact, so the guard is that nothing leaks in the first place).
    for name in files:
        if not name.endswith(".manifest.json"):
            continue
        raw = (d / name).read_bytes()
        for banned in (b"api_key", b"mailto", b"token", b"SECRET", FIXTURE_EMAIL.encode()):
            assert banned not in raw, (name, banned)
        assert pull_cordis.H2020_CSV_URL.encode() in raw or pull_cordis.HE_CSV_URL.encode() in raw

    # Collision-abort path: second run raises FileExistsError through the
    # write loop; every temp the pipeline created must be gone (finally's).
    try:
        with fake_session(zip_handler(h2020, he)) as session:
            pull_cordis.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    except FileExistsError:
        pass
    else:
        raise AssertionError("same-day collision must abort the real-config run")
    assert set(Path(tempfile.gettempdir()).glob("cordis-*")) == temps_before, (
        "download/repaired temps must be cleaned even when a write collision aborts the run"
    )


def g1_load_queries(tmp: Path) -> None:
    """Fixture → verbatim {package: [strings]}; guards; REAL config 5/16 (FR-1)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    loaded = pull_cordis.load_cordis_queries(cfg)
    assert loaded == {"pkgone": PKGONE_STRINGS, "pkgtwo": PKGTWO_STRINGS}, loaded
    # Missing cordis section → KeyError naming the config path.
    missing = write_fixture(tmp, "missing.yaml", "openalex:\n  pkg:\n    - 'filter=x'\n")
    try:
        pull_cordis.load_cordis_queries(missing)
    except KeyError as exc:
        assert "cordis" in str(exc) and str(missing) in str(exc), exc
    else:
        raise AssertionError("missing cordis section must raise KeyError")
    # Empty package cell → ValueError naming the package (queries.yaml contract;
    # pyyaml parses an empty cell as None — the guard must catch both).
    for empty_body in ("cordis:\n  pkg:\n", "cordis:\n  pkg: []\n"):
        empty = write_fixture(tmp, "empty.yaml", empty_body)
        try:
            pull_cordis.load_cordis_queries(empty)
        except ValueError as exc:
            assert "pkg" in str(exc), exc
        else:
            raise AssertionError(f"empty package cell must raise ValueError: {empty_body!r}")
    # Contract guard against the REAL config: 6 packages / 17 retuned strings
    # (issue-53 WI-02: p5 drops the over-bare `peptide` list, p6_gem enters at
    # the light budget), counts
    # {3,4,4,2,2,2}, every string lowercase comma-separated (D-4 grammar,
    # decisions.md 2026-09-26 "queries.yaml content contract" as scoped-
    # superseded 2026-10-05 by issue-53 D-02).
    real = pull_cordis.load_cordis_queries()
    counts = {package: len(qs) for package, qs in real.items()}
    assert counts == {
        "p5_venomate": 3,
        "p3_marketplace": 4,
        "p4_peptide_service": 4,
        "p2_insilico_workshop": 2,
        "p1_venospatial": 2,
        "p6_gem": 2,
    }, counts
    assert sum(counts.values()) == 17, counts
    for strings in real.values():
        for s in strings:
            assert s == s.lower(), s  # lowercase per D-4
            tokens = [t.strip() for t in s.split(",")]
            assert all(tokens), s  # comma-separated, no empty token


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("G-1", g1_load_queries),
        ("G-2", g2_match_rule),
        ("G-3", g3_download_stream),
        ("G-4", g4_probe_normalize_repair),
        ("G-5", g5_snapshots_manifests),
        ("G-6", g6_zero_match),
        ("G-7", g7_same_day_collision),
        ("G-8", g8_failure_degrades),
        ("G-INT", g_int_real_config),
    ):
        with tempfile.TemporaryDirectory(prefix="check_cordis_") as td:
            try:
                fn(Path(td))
            except Exception as exc:  # noqa: BLE001 — gate runner reports all
                print(f"{gate_id} FAIL: {exc!r}")
                failures += 1
            else:
                print(f"{gate_id} PASS")
    if failures:
        print(f"{failures} gate(s) FAILED")
        return 1
    print("all gates PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
