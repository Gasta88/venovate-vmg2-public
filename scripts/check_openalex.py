"""Ad-hoc verification gates for scripts/pull_openalex.py (issue-5).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_openalex.py

Prints `O-<n> PASS|FAIL` per gate (O-1…O-8 + O-INT integration) and exits
non-zero if any gate fails. Not part of the runtime pipeline. No gate touches
the network or real data/raw/ — httpx.MockTransport + temp dirs throughout
(same pattern as scripts/check_common.py).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import pull_openalex  # noqa: E402

# Fixture credentials — gates assert these NEVER appear in manifests/headers.
FIXTURE_KEY = "gate-secret-key"
FIXTURE_EMAIL = "gate@example.invalid"

# 2 packages: pkgone mixes a plain filter string with the group_by count
# string (only the latter may ever be executed); pkgtwo is group_by-only.
FIXTURE_TWO_PKG = """\
openalex:
  pkgone:
    - 'filter=title.search:"alpha beta",from_publication_date:2015-01-01'
    - 'filter=title.search:"alpha beta",from_publication_date:2015-01-01&group_by=publication_year'
  pkgtwo:
    - 'filter=abstract.search:"gamma delta",from_publication_date:2019-01-01&group_by=publication_year'
"""

FIXTURE_ONE_PKG = """\
openalex:
  pkgone:
    - 'filter=title.search:"alpha beta",from_publication_date:2015-01-01&group_by=publication_year'
"""

PKGONE_QUERY = 'filter=title.search:"alpha beta",from_publication_date:2015-01-01&group_by=publication_year'
PKGONE_FILTER_VALUE = 'title.search:"alpha beta",from_publication_date:2015-01-01'


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
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def fake_session(handler, *, api_key: str | None = FIXTURE_KEY):
    """ResilientClient over httpx.MockTransport — no network (check_common pattern).

    Mirrors _common.get_session()'s wiring: polite-pool mailto in User-Agent,
    OpenAlex key exposed on the session attribute only (never headers/params).
    """
    client = httpx.Client(
        headers={"User-Agent": f"venovate-vmg2/0.1 (+mailto:{FIXTURE_EMAIL})"},
        transport=httpx.MockTransport(handler),
    )
    return _common.ResilientClient(client, api_key_openalex=api_key)


def ok_page(groups: list[dict], next_cursor: str | None = None) -> httpx.Response:
    """Aggregation-mode 200 response with the locked {meta, group_by} shape."""
    return httpx.Response(
        200,
        json={
            "meta": {"count": sum(g.get("count", 0) for g in groups), "next_cursor": next_cursor},
            "group_by": groups,
        },
    )


def write_fixture(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(body, encoding="utf-8")
    return path


def o1_query_selection(tmp: Path) -> None:
    """Only &group_by= strings are selected; base_filter strips exactly (AC-3/4)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)
    selected = pull_openalex.load_group_by_queries(cfg)
    assert set(selected) == {"pkgone", "pkgtwo"}, selected
    assert selected["pkgone"] == [PKGONE_QUERY], selected  # plain string excluded
    assert all("&group_by=" in q for qs in selected.values() for q in qs), selected
    # base_filter keeps the verbatim fragment *including* the filter= prefix (FR-2).
    assert pull_openalex.base_filter(PKGONE_QUERY) == (
        'filter=title.search:"alpha beta",from_publication_date:2015-01-01'
    )
    # Missing openalex section → KeyError with the path in the message.
    missing = write_fixture(tmp, "missing.yaml", "ctgov:\n  pkg:\n    - 'query.term=x'\n")
    try:
        pull_openalex.load_group_by_queries(missing)
    except KeyError as exc:
        assert "openalex" in str(exc), exc
    else:
        raise AssertionError("missing openalex section must raise KeyError")
    # Package without a group_by string → ValueError (D-1 contract breach).
    empty = write_fixture(tmp, "empty.yaml", 'openalex:\n  pkg:\n    - \'filter=title.search:"x"\'\n')
    try:
        pull_openalex.load_group_by_queries(empty)
    except ValueError as exc:
        assert "pkg" in str(exc), exc
    else:
        raise AssertionError("package without group_by string must raise ValueError")


def o2_url_assembly(tmp: Path) -> None:
    """Captured requests: one group_by per call, value-only filter, params auth (AC-5/6)."""
    cfg = write_fixture(tmp, "one.yaml", FIXTURE_ONE_PKG)
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return ok_page([{"key": "2024", "key_display_name": "2024", "count": 1}])

    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(handler) as session:
        pull_openalex.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert len(captured) == 3, len(captured)  # 1 pkg × 1 string × 3 dims
    dims_seen: set[str] = set()
    for req in captured:
        assert req.url.host == "api.openalex.org" and req.url.path == "/works", req.url
        p = req.url.params
        assert p.get_list("group_by") == [p["group_by"]], p  # exactly one group_by
        dims_seen.add(p["group_by"])
        assert p["per-page"] == "200" and p["cursor"] == "*", p  # single page = first page
        assert p["api_key"] == FIXTURE_KEY and p["mailto"] == FIXTURE_EMAIL, p
        # Value-only filter param — guards against the double-prefix bug.
        assert p["filter"] == PKGONE_FILTER_VALUE, p
        assert not p["filter"].startswith("filter="), p
        # Credentials as params, never headers; UA keeps the polite-pool mailto.
        assert FIXTURE_KEY not in " ".join(req.headers.values()), req.headers
        assert FIXTURE_EMAIL not in [v for k, v in req.headers.items() if k.lower() != "user-agent"]
        assert FIXTURE_EMAIL in req.headers["User-Agent"], req.headers["User-Agent"]
    assert dims_seen == set(pull_openalex.DIMENSIONS), dims_seen


def o3_aggregations_only(tmp: Path) -> None:
    """Every issued URL carries group_by=; plain filter strings → zero requests (AC-3)."""
    cfg = write_fixture(tmp, "two.yaml", FIXTURE_TWO_PKG)  # pkgone has 1 plain string
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert "group_by" in req.url.params, f"non-aggregate request issued on {req.url.path}"
        seen.append(req)
        return ok_page([])

    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(handler) as session:
        pull_openalex.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    # 2 group_by strings × 3 dims = 6; the plain filter strings produced NOTHING.
    assert len(seen) == 6, len(seen)


def o4_cursor_loop(tmp: Path) -> None:
    """Two mocked pages merge in page order; loop stops on null next_cursor (AC-6)."""
    cursors: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        cursor = req.url.params["cursor"]
        cursors.append(cursor)
        if cursor == "*":
            return ok_page(
                [
                    {"key": "US", "key_display_name": "United States", "count": 120},
                    {"key": "GBR", "key_display_name": "United Kingdom", "count": 33},
                ],
                next_cursor="abc",
            )
        return ok_page([{"key": "DEU", "key_display_name": "Germany", "count": 11}])

    with fake_session(handler) as session:
        df, first_url = pull_openalex.fetch_groups(
            session,
            'filter=title.search:"alpha beta",from_publication_date:2015-01-01',
            "authorships.countries",
            api_key=FIXTURE_KEY,
            mailto=FIXTURE_EMAIL,
        )
    assert cursors == ["*", "abc"], cursors  # exactly two requests; no third
    assert list(df.columns) == ["key", "key_display_name", "count"], df.columns
    assert list(df["key"]) == ["US", "GBR", "DEU"], df  # merged, page order kept
    assert list(df["count"]) == [120, 33, 11], df
    assert "cursor=%2A" in first_url, first_url  # manifest URL = first page


def o5_snapshot_shape(tmp: Path) -> None:
    """1-pkg fixture → 3 stem pairs, exact CSV columns, verbatim manifest query."""
    cfg = write_fixture(tmp, "one.yaml", FIXTURE_ONE_PKG)
    groups = [
        {"key": "US", "key_display_name": "United States", "count": 120},
        {"key": "GBR", "key_display_name": "United Kingdom", "count": 33},
    ]

    def handler(_req: httpx.Request) -> httpx.Response:
        return ok_page(groups)

    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(handler) as session:
        paths = pull_openalex.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert len(paths) == 3
    date_dirs = list((tmp / "raw" / "openalex").iterdir())
    assert len(date_dirs) == 1, date_dirs
    stems = {f"pkgone-{d}" for d in pull_openalex.DIMENSIONS}
    names = sorted(p.name for p in date_dirs[0].iterdir())
    assert names == sorted([f"{s}.csv" for s in stems] + [f"{s}.manifest.json" for s in stems]), names
    csv = date_dirs[0] / "pkgone-authorships.countries.csv"
    raw = csv.read_bytes()
    assert raw.splitlines()[0] == b"key,key_display_name,count", raw  # exact columns (FR-4)
    assert b"\r\n" not in raw  # LF-terminated bytes (reproducible sha256)
    manifest = json.loads(
        (date_dirs[0] / "pkgone-authorships.countries.manifest.json").read_text("utf-8")
    )
    assert manifest["query"] == PKGONE_QUERY  # verbatim config string (AC-4)
    assert manifest["source"] == "openalex" and manifest["format"] == "csv"
    assert manifest["record_count"] == len(groups) == len(pd.read_csv(csv))
    assert manifest["snapshot"] == "pkgone-authorships.countries.csv"

    # Edge cases: entry without key_display_name → key fallback (never null);
    # empty group_by array → header-only CSV with record_count 0 (valid snapshot).
    def edge_handler(req: httpx.Request) -> httpx.Response:
        if req.url.params["group_by"] == "authorships.institutions.id":
            return ok_page([{"key": "https://openalex.org/I123", "count": 5}])
        return ok_page([])

    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(edge_handler) as session:
        pull_openalex.pull_all(session, base_dir=tmp / "raw2", config_path=cfg)
    d2 = next((tmp / "raw2" / "openalex").iterdir())
    inst = (d2 / "pkgone-authorships.institutions.id.csv").read_bytes()
    assert inst == b"key,key_display_name,count\nhttps://openalex.org/I123,https://openalex.org/I123,5\n", inst
    year = (d2 / "pkgone-publication_year.csv").read_bytes()
    assert year == b"key,key_display_name,count\n", year
    man_year = json.loads((d2 / "pkgone-publication_year.manifest.json").read_text("utf-8"))
    assert man_year["record_count"] == 0, man_year


def o6_redaction(tmp: Path) -> None:
    """Manifests carry api_key=*** / mailto=***, never the fixture secret/email."""
    cfg = write_fixture(tmp, "one.yaml", FIXTURE_ONE_PKG)

    def handler(_req: httpx.Request) -> httpx.Response:
        return ok_page([{"key": "US", "key_display_name": "United States", "count": 1}])

    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(handler) as session:
        paths = pull_openalex.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
    assert len(paths) == 3
    for snap in paths:
        raw = (snap.parent / f"{snap.stem}.manifest.json").read_bytes()
        assert b"api_key=***" in raw and b"mailto=***" in raw, raw
        assert FIXTURE_KEY.encode() not in raw and FIXTURE_EMAIL.encode() not in raw, raw


def o7_missing_key_hardfail(tmp: Path) -> None:
    """Unset OPENALEX_API_KEY → non-zero exit + .env message, zero requests (AC-8).

    Subprocess isolation mirrors check_common.g8: scrubbed env + cwd=tmp + a
    `-c` runner. The `-c` form matters: with no __main__.__file__, python-dotenv
    resolves .env from cwd (tmp — empty), so a developer's repo-root .env can
    never leak into this gate; running the script by path instead would walk up
    from scripts/ and load it (verified empirically 2026-09-26).
    """
    env = {
        k: v for k, v in os.environ.items() if k not in ("OPENALEX_API_KEY", "CONTACT_EMAIL")
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, "-c", "import sys, pull_openalex; sys.exit(pull_openalex.main())"],
        cwd=tmp, env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0, (result.returncode, result.stdout, result.stderr)
    assert "OPENALEX_API_KEY" in result.stderr, result.stderr
    assert ".env" in result.stderr, result.stderr
    assert not (tmp / "data").exists()  # hard-fail precedes any request or write


def o8_same_day_collision(tmp: Path) -> None:
    """Second pull_all into the same base_dir raises FileExistsError, files untouched."""
    cfg = write_fixture(tmp, "one.yaml", FIXTURE_ONE_PKG)

    def handler(_req: httpx.Request) -> httpx.Response:
        return ok_page([{"key": "US", "key_display_name": "United States", "count": 1}])

    def dir_bytes(root: Path) -> dict[str, bytes]:
        # root/<source>/<date>/<file> — three levels below the base_dir.
        return {str(p.relative_to(root)): p.read_bytes() for p in root.glob("*/*/*")}

    with env_overlay(CONTACT_EMAIL=FIXTURE_EMAIL), fake_session(handler) as session:
        pull_openalex.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
        before = dir_bytes(tmp / "raw")
        assert len(before) == 6, before  # 3 csv + 3 manifest
        try:
            pull_openalex.pull_all(session, base_dir=tmp / "raw", config_path=cfg)
        except FileExistsError:
            pass
        else:
            raise AssertionError("same-day re-pull must raise FileExistsError (AC-7)")
        assert dir_bytes(tmp / "raw") == before  # no overwrite, no manifest rewrite


def o_int_error_path(tmp: Path) -> None:
    """Non-2xx → credential-free RuntimeError, exactly one attempt (FR-7)."""
    attempts = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad filter"})

    with fake_session(handler) as session:
        try:
            pull_openalex.fetch_groups(
                session,
                'filter=title.search:"alpha beta",from_publication_date:2015-01-01',
                "authorships.countries",
                api_key=FIXTURE_KEY,
                mailto=FIXTURE_EMAIL,
            )
        except RuntimeError as exc:
            msg = str(exc)
            assert "400" in msg and "authorships.countries" in msg, msg
            assert FIXTURE_KEY not in msg and FIXTURE_EMAIL not in msg, msg
        else:
            raise AssertionError("non-2xx must raise RuntimeError (FR-7)")
    assert attempts["n"] == 1, attempts  # 400 non-retryable — _common policy untouched


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("O-1", o1_query_selection),
        ("O-2", o2_url_assembly),
        ("O-3", o3_aggregations_only),
        ("O-4", o4_cursor_loop),
        ("O-5", o5_snapshot_shape),
        ("O-6", o6_redaction),
        ("O-7", o7_missing_key_hardfail),
        ("O-8", o8_same_day_collision),
        ("O-INT", o_int_error_path),
    ):
        with tempfile.TemporaryDirectory(prefix="check_openalex_") as td:
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
