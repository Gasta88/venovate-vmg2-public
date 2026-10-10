"""Ad-hoc verification gates for scripts/pull_ctgov.py (issue-6).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_ctgov.py

Prints `C-<n> PASS|FAIL` per gate (C-1…C-8 + C-INT) and exits non-zero if any
gate fails. Not part of the runtime pipeline. No gate touches the network or
real data/raw/ — httpx.MockTransport + temp dirs throughout (same pattern as
scripts/check_openalex.py). Gate fixtures emit the LIVE-VERIFIED v2 envelope
key `studies` (specs/issue-6-extdocs.md CRITICAL finding — NOT `entries`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import pull_ctgov  # noqa: E402

# Fixture identity — gates assert the email NEVER appears in credential params
# and that pull_ctgov adds no auth of its own (AC1: keyless public endpoint).
FIXTURE_EMAIL = "gate@example.invalid"

# Real-shaped fragments (queries.yaml grammar): AREA[...], spaces, quotes, and
# the mandatory trailing status filter, passed through verbatim.
SUFFIX = "&filter.overallStatus=RECRUITING,COMPLETED"
PKGONE_Q1 = 'query.term=AREA[ConditionSearch]"alpha one" AND AREA[StudyType]INTERVENTIONAL' + SUFFIX
PKGONE_Q2 = 'query.term=AREA[ConditionSearch]"alpha two" AND AREA[StudyType]INTERVENTIONAL' + SUFFIX
PKGTWO_Q1 = 'query.term=AREA[BriefTitle]"beta only" AND AREA[StudyType]INTERVENTIONAL' + SUFFIX

# 2 packages: pkgone has 2 overlapping query strings (dedupe across queries),
# pkgtwo has 1. Mirrors the spec's Test Strategy fixture shape.
FIXTURE_TWO_PKG = f"""\
ctgov:
  pkgone:
    - '{PKGONE_Q1}'
    - '{PKGONE_Q2}'
  pkgtwo:
    - '{PKGTWO_Q1}'
"""

# pkgone with 3 queries — exercises the mid-package-failure edge (query 2 400s).
PKGONE_Q3 = 'query.term=AREA[ConditionSearch]"alpha three" AND AREA[StudyType]INTERVENTIONAL' + SUFFIX
FIXTURE_THREE_Q = f"""\
ctgov:
  pkgone:
    - '{PKGONE_Q1}'
    - '{PKGONE_Q2}'
    - '{PKGONE_Q3}'
  pkgtwo:
    - '{PKGTWO_Q1}'
"""


@contextmanager
def env_overlay(**vars):
    """Temporarily set env vars (None removes); always restores prior state."""
    saved = {k: os.environ.get(k) for k in vars}
    try:
        for k, v in vars.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if k in vars:
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def fake_session(handler, *, polite_ua: bool = False):
    """ResilientClient over httpx.MockTransport — no network (check_common pattern).

    polite_ua mirrors _common.get_session()'s User-Agent derivation from the
    real environment (_common.contact_email()); the full get_session wiring is
    check_common.py's turf — here we verify pull_ctgov never adds auth of its
    own and succeeds unauthenticated (AC1).
    """
    headers = {}
    if polite_ua:
        email = _common.contact_email()
        if email:
            headers["User-Agent"] = f"venovate-vmg2/0.1 (+mailto:{email})"
    client = httpx.Client(headers=headers, transport=httpx.MockTransport(handler))
    return _common.ResilientClient(client)


def study(nct_id: str | None, title: str = "t") -> dict:
    """One full v2 study object (raw-fidelity payload of a JSONL line)."""
    identification = {"briefTitle": title}
    if nct_id is not None:
        identification["nctId"] = nct_id
    return {
        "protocolSection": {
            "identificationModule": identification,
            "statusModule": {"overallStatus": "RECRUITING"},
        }
    }


def ok_page(studies: list[dict], next_token: str | None = None) -> httpx.Response:
    """200 with the live-verified {"studies", "nextPageToken"} envelope.

    CRITICAL (specs/issue-6-extdocs.md): the top-level key is `studies`, NOT
    `entries` — fixtures shaped like `entries` would make gates pass against a
    wrong parser and every live snapshot come back empty.
    """
    payload: dict = {"studies": studies}
    if next_token is not None:
        payload["nextPageToken"] = next_token
    return httpx.Response(200, json=payload)


def write_fixture(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(body, encoding="utf-8")
    return path


def date_dir(tmp_raw: Path, source: str = "ctgov") -> Path:
    dirs = list((tmp_raw / source).iterdir())
    assert len(dirs) == 1, dirs
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", dirs[0].name), dirs[0].name
    return dirs[0]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def nct_of(record: dict) -> str | None:
    return (
        ((record.get("protocolSection") or {}).get("identificationModule") or {})
    ).get("nctId")


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def c1_load_queries(tmp: Path) -> None:
    """Fixture → verbatim {package: [strings]}; guards; REAL config 5/13 (FR1/AC2)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    loaded = pull_ctgov.load_ctgov_queries(cfg)
    assert loaded == {"pkgone": [PKGONE_Q1, PKGONE_Q2], "pkgtwo": [PKGTWO_Q1]}, loaded
    # Missing ctgov section → KeyError naming the config path.
    missing = write_fixture(tmp, "missing.yaml", "openalex:\n  pkg:\n    - 'filter=x'\n")
    try:
        pull_ctgov.load_ctgov_queries(missing)
    except KeyError as exc:
        assert "ctgov" in str(exc) and str(missing) in str(exc), exc
    else:
        raise AssertionError("missing ctgov section must raise KeyError")
    # Empty package cell → ValueError naming the package (queries.yaml contract).
    empty = write_fixture(tmp, "empty.yaml", "ctgov:\n  pkg:\n")
    try:
        pull_ctgov.load_ctgov_queries(empty)
    except ValueError as exc:
        assert "pkg" in str(exc), exc
    else:
        raise AssertionError("empty package cell must raise ValueError")
    # Contract guard against the REAL config: 5 pulled ctgov cells (six-key
    # universe minus p2_insilico_workshop, role n/a per evidence_map) / 14
    # retuned strings, every string ending with the overallStatus filter
    # (issue-53 WI-02; decisions.md issue-2 block as scoped-superseded 2026-10-05).
    real = pull_ctgov.load_ctgov_queries()
    counts = {package: len(qs) for package, qs in real.items()}
    assert counts == {
        "p5_venomate": 4,
        "p3_marketplace": 3,
        "p4_peptide_service": 4,
        "p1_venospatial": 2,
        "p6_gem": 1,
    }, counts
    assert sum(counts.values()) == 14, counts
    assert all(
        q.endswith("&filter.overallStatus=RECRUITING,COMPLETED")
        for qs in real.values()
        for q in qs
    ), "status filter must be verbatim-present in every real string"


def c2_url_assembly(tmp: Path) -> None:
    """Verbatim fragment on the URL, pageSize=1000, no cursor/auth params, UA (AC1-3/7)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return ok_page([study("NCT00000001")])

    # Polite-pool path: CONTACT_EMAIL set → get_session-derived UA reaches requests.
    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(handler, polite_ua=True) as session:
        pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert len(captured) == 3, len(captured)  # pkgone q1, q2; pkgtwo q1 (dict order)
    for req, fragment in zip(captured, [PKGONE_Q1, PKGONE_Q2, PKGTWO_Q1]):
        assert req.url.host == "clinicaltrials.gov", req.url
        assert req.url.path == "/api/v2/studies", req.url
        # Decoded params equal the config fragment verbatim + pageSize=1000 —
        # httpx percent-encoding of " and space is semantically identical.
        assert list(req.url.params.multi_items()) == parse_qsl(
            f"{fragment}&pageSize=1000"
        ), req.url.query
        assert req.url.params.get("pageToken") is None, req.url  # first page: no cursor
        assert not {"api_key", "key", "token", "mailto"} & set(
            req.url.params.keys()
        ), req.url.params  # keyless endpoint (AC1)
        assert req.headers.get("User-Agent") == f"venovate-vmg2/0.1 (+mailto:{FIXTURE_EMAIL})"
    # Unauthenticated path (AC1): CONTACT_EMAIL unset → no mailto anywhere,
    # requests still issued and snapshots still written.
    captured.clear()
    with env_overlay(CONTACT_EMAIL=None), fake_session(handler) as session:
        paths, failures = pull_ctgov.pull_all(session, base_dir=tmp / "raw2", config_path=cfg)
    assert len(captured) == 3 and failures == [], (len(captured), failures)
    assert len(paths) == 2
    for req in captured:
        assert FIXTURE_EMAIL not in " ".join(req.headers.values()), req.headers


def c3_page_token_loop(tmp: Path) -> None:
    """nextPageToken followed once; entries merged in page order; URL = first page (AC3/6, FR4)."""
    tokens: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        token = req.url.params.get("pageToken")
        tokens.append(token)
        if token is None:
            return ok_page(
                [study("NCT00000001", "one"), study("NCT00000002", "two")],
                next_token="tok-2",
            )
        # Regression guard: httpx 0.28 get(params=) would REPLACE the URL query
        # and drop the fragment — the cursor page must carry fragment+pageSize+token.
        assert list(req.url.params.multi_items()) == parse_qsl(
            f"{PKGONE_Q1}&pageSize=1000&pageToken=tok-2"
        ), req.url.query
        return ok_page([study("NCT00000003", "three")])

    with fake_session(handler) as session:
        studies, first_url = pull_ctgov.fetch_studies(session, PKGONE_Q1)
    assert tokens == [None, "tok-2"], tokens  # exactly two requests; no third
    assert [nct_of(s) for s in studies] == [
        "NCT00000001",
        "NCT00000002",
        "NCT00000003",
    ], studies  # merged in page order
    assert "pageToken" not in first_url and "pageSize=1000" in first_url, first_url
    assert parse_qsl(urlsplit(first_url).query) == parse_qsl(f"{PKGONE_Q1}&pageSize=1000"), first_url
    # Edge: nextPageToken present but empty string → falsy → loop terminates.
    requests = {"n": 0}

    def empty_token_handler(req: httpx.Request) -> httpx.Response:
        requests["n"] += 1
        return ok_page([study("NCT00000004")], next_token="")

    with fake_session(empty_token_handler) as session:
        studies2, _ = pull_ctgov.fetch_studies(session, PKGONE_Q2)
    assert requests["n"] == 1 and len(studies2) == 1, requests


def c4_dedupe(tmp: Path) -> None:
    """nctId dedupe across pages AND queries, first wins, no-nctId kept appended (AC5)."""

    def handler(req: httpx.Request) -> httpx.Response:
        term = req.url.params["query.term"]
        if "alpha one" in term:
            if req.url.params.get("pageToken") is None:
                return ok_page(
                    [
                        study("NCT00000001", "first-seen"),
                        study("NCT00000001", "within-page-dup"),  # dup within one page
                        study("NCT00000002", "q1-wins"),
                        study(None, "no-nct-id"),  # defensive edge (Risk 5)
                    ],
                    next_token="p2",
                )
            return ok_page([study("NCT00000002", "across-page-dup")])  # dup across pages
        if "alpha two" in term:
            return ok_page([study("NCT00000001", "across-query-dup"), study("NCT00000003", "three")])
        return ok_page([study("NCT00000001", "pkgtwo-keeps-its-own")])

    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    with fake_session(handler) as session:
        paths, failures = pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert failures == [], failures
    d = date_dir(tmp / "raw")
    lines = read_jsonl(d / "pkgone.jsonl")
    assert [nct_of(r) for r in lines] == [
        "NCT00000001",
        "NCT00000002",
        "NCT00000003",
        None,
    ], lines  # first-seen order; no-nctId record appended last
    titles = [r["protocolSection"]["identificationModule"]["briefTitle"] for r in lines]
    assert titles == ["first-seen", "q1-wins", "three", "no-nct-id"], titles  # first wins
    # Dedupe is PER PACKAGE: pkgtwo keeps its own copy of NCT00000001.
    assert [nct_of(r) for r in read_jsonl(d / "pkgtwo.jsonl")] == ["NCT00000001"]


def c5_snapshot_manifest(tmp: Path) -> None:
    """Stem pairs under ctgov/<date>/, raw-fidelity JSONL, full manifest fields (AC4/6)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    rec_a, rec_b, rec_c = study("NCT00000001", "one"), study("NCT00000002", "two"), study("NCT00000003", "three")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        term = req.url.params["query.term"]
        if "alpha one" in term:
            return ok_page([rec_a, rec_b])
        if "alpha two" in term:
            return ok_page([rec_b])  # overlap → deduped
        return ok_page([rec_c])

    with fake_session(handler) as session:
        paths, failures = pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert failures == [] and len(paths) == 2, (failures, paths)
    d = date_dir(tmp / "raw")
    names = sorted(p.name for p in d.iterdir())
    assert names == sorted(
        ["pkgone.jsonl", "pkgone.manifest.json", "pkgtwo.jsonl", "pkgtwo.manifest.json"]
    ), names  # #5 stem-pair convention (AC4)
    lines = read_jsonl(d / "pkgone.jsonl")
    assert lines == [rec_a, rec_b], lines  # raw fidelity: full study objects
    raw = (d / "pkgone.jsonl").read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw, raw
    manifest = json.loads((d / "pkgone.manifest.json").read_text("utf-8"))
    assert manifest["source"] == "ctgov" and manifest["format"] == "jsonl"
    assert manifest["query"] == PKGONE_Q1, manifest  # first string, verbatim (AC2)
    assert manifest["record_count"] == 2 == len(lines), manifest
    assert manifest["snapshot"] == "pkgone.jsonl"
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest(), manifest  # re-derivable
    # AC6: url = first request URL of the package (_redact_url applied — a
    # credential no-op for ctgov; decoded-params check pins the right request).
    assert manifest["url"] == _common._redact_url(str(captured[0].url)), manifest["url"]
    assert parse_qsl(urlsplit(manifest["url"]).query) == parse_qsl(f"{PKGONE_Q1}&pageSize=1000")
    manifest2 = json.loads((d / "pkgtwo.manifest.json").read_text("utf-8"))
    assert manifest2["query"] == PKGTWO_Q1
    assert manifest2["url"] == _common._redact_url(str(captured[2].url))


def c6_same_day_collision(tmp: Path) -> None:
    """Second pull_all into the same base_dir raises FileExistsError, bytes unchanged (AC8)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)

    def handler(_req: httpx.Request) -> httpx.Response:
        return ok_page([study("NCT00000001")])

    def dir_bytes(root: Path) -> dict[str, bytes]:
        # root/<source>/<date>/<file> — three levels below the base_dir.
        return {str(p.relative_to(root)): p.read_bytes() for p in root.glob("*/*/*")}

    with fake_session(handler) as session:
        pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
        before = dir_bytes(tmp / "raw")
        assert len(before) == 4, before  # 2 jsonl + 2 manifest
        try:
            pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
        except FileExistsError:
            pass
        else:
            raise AssertionError("same-day re-pull must raise FileExistsError (AC8)")
        assert dir_bytes(tmp / "raw") == before  # no overwrite, no manifest rewrite


def c7_failure_degrades(tmp: Path) -> None:
    """Fetch failure warns + continues + exit 1; success exits 0; collision NOT swallowed (AC9/FR5/6, AC8)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)

    def handler(req: httpx.Request) -> httpx.Response:
        if "alpha two" in req.url.params["query.term"]:
            return httpx.Response(400, json={"error": "bad query"})  # non-retryable → fast
        return ok_page([study("NCT00000001")])

    capture = _LogCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    try:
        with fake_session(handler) as session:
            paths, failures = pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    finally:
        root.removeHandler(capture)
    assert failures == ["pkgone"], failures
    assert [p.name for p in paths] == ["pkgtwo.jsonl"], paths
    d = date_dir(tmp / "raw")
    assert not (d / "pkgone.jsonl").exists(), "failed package must leave no partial snapshot"
    warnings = [r.getMessage() for r in capture.records if r.levelno == logging.WARNING]
    assert any("pkgone" in m for m in warnings), warnings  # logging.warning contract

    # main() exit-code contract via the in-process DI seam (no network, no real data/raw/).
    with fake_session(handler) as session:
        rc = pull_ctgov.main(session=session, base_dir=tmp / "raw2", config_path=cfg)
    assert rc == 1, rc  # FR6: any package failed
    with fake_session(lambda req: ok_page([study("NCT00000009")])) as session:
        rc = pull_ctgov.main(session=session, base_dir=tmp / "raw3", config_path=cfg)
    assert rc == 0, rc  # all-success
    # Injected sessions are the caller's: main() must not close them.
    with fake_session(lambda req: ok_page([study("NCT00000009")])) as session:
        assert pull_ctgov.main(session=session, base_dir=tmp / "raw4", config_path=cfg) == 0
        assert len(pull_ctgov.pull_all(session, base_dir=tmp / "raw5", config_path=cfg)[0]) == 2

    # Collision outranks degrade (decisions.md 2026-09-28): FileExistsError
    # propagates out of main() — it is NOT swallowed into `failures`.
    try:
        with fake_session(lambda req: ok_page([study("NCT00000009")])) as session:
            pull_ctgov.main(session=session, base_dir=tmp / "raw3", config_path=cfg)
    except FileExistsError:
        pass
    else:
        raise AssertionError("same-day collision must propagate FileExistsError through main (AC8)")


def c8_mid_package_and_zero_matches(tmp: Path) -> None:
    """Query 2 of 3 failing → whole package absent; zero-match package → valid empty snapshot."""
    cfg = write_fixture(tmp, "three.yaml", FIXTURE_THREE_Q)

    def handler(req: httpx.Request) -> httpx.Response:
        if "alpha two" in req.url.params["query.term"]:
            return httpx.Response(400, json={"error": "bad query"})
        return ok_page([study("NCT00000001")])

    with fake_session(handler) as session:
        paths, failures = pull_ctgov.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert failures == ["pkgone"], failures  # one bad query condemns the whole package
    assert [p.name for p in paths] == ["pkgtwo.jsonl"], paths
    d = date_dir(tmp / "raw")
    assert not (d / "pkgone.jsonl").exists(), "no partial snapshot for a failed package"
    assert (d / "pkgtwo.jsonl").exists() and (d / "pkgtwo.manifest.json").exists()

    # Edge: zero matches on every query → empty JSONL + record_count 0 (valid
    # snapshot, mirrors the openalex header-only-CSV precedent).
    with fake_session(lambda req: ok_page([])) as session:
        paths, failures = pull_ctgov.pull_all(session, base_dir=tmp / "raw2", config_path=cfg)
    assert failures == [] and len(paths) == 2, (failures, paths)
    d2 = date_dir(tmp / "raw2")
    for snap in (d2 / "pkgone.jsonl", d2 / "pkgtwo.jsonl"):
        assert snap.read_bytes() == b"", snap
        manifest = json.loads((snap.parent / f"{snap.stem}.manifest.json").read_text("utf-8"))
        assert manifest["record_count"] == 0, manifest
        assert manifest["sha256"] == hashlib.sha256(b"").hexdigest(), manifest


def c_int_error_hygiene(tmp: Path) -> None:
    """RuntimeError names status+path, never the query; 5xx retried by ResilientClient (FR7 idiom)."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad filter"})

    with fake_session(handler) as session:
        try:
            pull_ctgov.fetch_studies(session, PKGONE_Q1)
        except RuntimeError as exc:
            msg = str(exc)
            assert "400" in msg and "/api/v2/studies" in msg, msg
            assert "query.term" not in msg and "AREA" not in msg, msg  # path-only message
        else:
            raise AssertionError("non-200 must raise RuntimeError")

    # 5xx → ResilientClient backoff retries (policy inherited, untouched);
    # one 503 then 200 → success after exactly two attempts.
    attempts = {"n": 0}

    def flaky(_req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return ok_page([study("NCT00000001")])

    with fake_session(flaky) as session:
        studies, _ = pull_ctgov.fetch_studies(session, PKGONE_Q1)
    assert attempts["n"] == 2, attempts
    assert [nct_of(s) for s in studies] == ["NCT00000001"], studies


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("C-1", c1_load_queries),
        ("C-2", c2_url_assembly),
        ("C-3", c3_page_token_loop),
        ("C-4", c4_dedupe),
        ("C-5", c5_snapshot_manifest),
        ("C-6", c6_same_day_collision),
        ("C-7", c7_failure_degrades),
        ("C-8", c8_mid_package_and_zero_matches),
        ("C-INT", c_int_error_hygiene),
    ):
        with tempfile.TemporaryDirectory(prefix="check_ctgov_") as td:
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
