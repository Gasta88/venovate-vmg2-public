"""Ad-hoc verification gates for scripts/_common.py (issue-4).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_common.py

Prints `G-<n> PASS|FAIL` per gate (G-1…G-8 + G-INT integration dry run) and
exits non-zero if any gate fails. Not part of the runtime pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class sleep_recorder:
    """Patch time.sleep to record requested delays instead of waiting."""

    def __enter__(self):
        self.delays: list[float] = []
        self._real = time.sleep
        time.sleep = self.delays.append
        return self

    def __exit__(self, *exc):
        time.sleep = self._real


def fake_client(handler):
    """ResilientClient over httpx.MockTransport — no network, per extdocs."""
    return _common.ResilientClient(httpx.Client(transport=httpx.MockTransport(handler)))


def g1_layout(tmp: Path) -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    snap = _common.write_snapshot(df, "demo", "q", base_dir=tmp)
    rel = snap.relative_to(tmp)
    assert rel.parts[0] == "demo", rel
    assert DATE_RE.fullmatch(rel.parts[1]), rel
    assert rel.parts[1] == time.strftime("%Y-%m-%d", time.gmtime()), rel


def g2_formats(tmp: Path) -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    csv_path = _common.write_snapshot(df, "demo", "q", base_dir=tmp, name="frame")
    assert csv_path.suffix == ".csv"
    assert len(pd.read_csv(csv_path)) == len(df)  # round-trip, same row count
    jsonl_path = _common.write_snapshot(
        [{"k": 1}, {"k": 2}], "demo", "q", base_dir=tmp, name="rows"
    )
    assert jsonl_path.suffix == ".jsonl"
    lines = jsonl_path.read_text("utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"k": 1}, {"k": 2}]


def _manifest_for(tmp: Path, snap: Path) -> dict:
    manifest_path = snap.parent / f"{snap.stem}.manifest.json"
    assert manifest_path.exists(), manifest_path
    assert manifest_path.parent == snap.parent  # next to the snapshot
    return json.loads(manifest_path.read_text("utf-8"))


def g3_manifest_fields(tmp: Path) -> None:
    snap = _common.write_snapshot(pd.DataFrame({"a": [1]}), "demo", "q", base_dir=tmp)
    manifest = _manifest_for(tmp, snap)
    locked = {"query", "url", "timestamp", "record_count", "sha256"}
    assert locked <= set(manifest), locked - set(manifest)
    assert {"source", "snapshot", "format"} <= set(manifest)
    assert manifest["source"] == "demo" and manifest["snapshot"] == snap.name
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest["timestamp"]), manifest


def g4_sha_and_no_overwrite(tmp: Path) -> None:
    df = pd.DataFrame({"a": [1, 2]})
    snap = _common.write_snapshot(df, "demo", "q", base_dir=tmp, name="once")
    assert _manifest_for(tmp, snap)["sha256"] == hashlib.sha256(snap.read_bytes()).hexdigest()
    try:
        _common.write_snapshot(df, "demo", "q", base_dir=tmp, name="once")
    except FileExistsError:
        pass
    else:
        raise AssertionError("same-name second write must raise FileExistsError (FR-4)")


def g5_retry_policy() -> None:
    with sleep_recorder() as slept:
        # 429 then 200: succeeds in 2 attempts.
        attempts = {"n": 0}

        def flaky_429(_req):
            attempts["n"] += 1
            return httpx.Response(429 if attempts["n"] == 1 else 200)

        assert fake_client(flaky_429).get("https://api.test/x").status_code == 200
        assert attempts["n"] == 2 and len(slept.delays) == 1

        # Persistent 503: stops after 6 attempts, returns the 503; backoff 1,2,4,8,16 ±10%.
        attempts["n"] = 0
        slept.delays.clear()

        def always_503(_req):
            attempts["n"] += 1
            return httpx.Response(503)

        resp = fake_client(always_503).get("https://api.test/x")
        assert resp.status_code == 503 and attempts["n"] == 6, (resp.status_code, attempts["n"])
        assert len(slept.delays) == 5
        assert all(0.9 * 2**i <= d <= 1.1 * 2**i for i, d in enumerate(slept.delays)), slept.delays

        # 404: returns immediately, no retry.
        attempts["n"] = 0
        slept.delays.clear()

        def always_404(_req):
            attempts["n"] += 1
            return httpx.Response(404)

        assert fake_client(always_404).get("https://api.test/x").status_code == 404
        assert attempts["n"] == 1 and slept.delays == []

        # Retry-After: 2 honored (requested sleep in [2s, 2.5s]).
        attempts["n"] = 0
        slept.delays.clear()

        def with_retry_after(_req):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
            return httpx.Response(200)

        assert fake_client(with_retry_after).get("https://api.test/x").status_code == 200
        assert len(slept.delays) == 1 and 2.0 <= slept.delays[0] <= 2.5, slept.delays

        # Transport errors retried, then re-raised after the 6th attempt.
        attempts["n"] = 0
        slept.delays.clear()

        def always_down(_req):
            attempts["n"] += 1
            raise httpx.ConnectError("down")

        try:
            fake_client(always_down).get("https://api.test/x")
        except httpx.TransportError:
            assert attempts["n"] == 6 and len(slept.delays) == 5
        else:
            raise AssertionError("transport error must re-raise after retries")

    # Backoff band capped at BACKOFF_CAP (120s, raised in the "minor adjustments"
    # commit) with the +10% jitter ceiling; Retry-After capped at RETRY_AFTER_CAP.
    assert all(0.9 * min(120.0, 2.0**a) <= _common._backoff_delay(a) <= 1.1 * min(120.0, 2.0**a) for a in (5, 10, 20))
    assert _common._retry_after_delay(httpx.Response(429, headers={"Retry-After": "999"})) == 120.0
    # HTTP-date Retry-After is ignored (edge case).
    assert _common._retry_after_delay(
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    ) is None


def g6_session_env_wiring() -> None:
    saved = {k: os.environ.get(k) for k in ("CONTACT_EMAIL", "OPENALEX_API_KEY")}
    real_load = _common.load_dotenv
    _common.load_dotenv = lambda *a, **k: False  # simulate .env absent
    try:
        # Fresh-clone case: no vars -> usable client, no mailto UA, key None.
        for k in saved:
            os.environ.pop(k, None)
        with _common.get_session() as session:
            assert session.api_key_openalex is None
            assert "mailto" not in session.headers.get("User-Agent", "")
            assert hasattr(session, "get") and hasattr(session, "post")
        # Polite-pool case: email in UA; key exposed on the session only.
        os.environ["CONTACT_EMAIL"] = "gate@example.invalid"
        os.environ["OPENALEX_API_KEY"] = "gate-secret-key"
        with _common.get_session() as session:
            assert "gate@example.invalid" in session.headers["User-Agent"]
            assert session.api_key_openalex == "gate-secret-key"
            assert "gate-secret-key" not in str(dict(session.headers))
    finally:
        _common.load_dotenv = real_load
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def g7_url_redaction(tmp: Path) -> None:
    snap = _common.write_snapshot(
        pd.DataFrame({"a": [1]}),
        "demo",
        "q",
        url="https://x/y?api_key=SECRET&mailto=a@b.c&keep=1",
        base_dir=tmp,
    )
    raw = (snap.parent / f"{snap.stem}.manifest.json").read_bytes()
    assert b"api_key=***" in raw and b"mailto=***" in raw and b"keep=1" in raw, raw
    assert b"SECRET" not in raw and b"a@b.c" not in raw, raw


def g8_type_error_and_import_guard(tmp: Path) -> None:
    try:
        _common.write_snapshot("string", "demo", "q", base_dir=tmp)
    except TypeError:
        pass
    else:
        raise AssertionError("str input must raise TypeError")
    # Path-traversal guard on the source component.
    for bad in ("../evil", "a/b", ""):
        try:
            _common.write_snapshot([{}], bad, "q", base_dir=tmp)
        except ValueError:
            pass
        else:
            raise AssertionError(f"source {bad!r} must raise ValueError")
    # Import performs no network/env reads: clean env + foreign cwd, no .env.
    env = {k: v for k, v in os.environ.items() if k not in ("CONTACT_EMAIL", "OPENALEX_API_KEY")}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, "-c", "import _common"], cwd=tmp, env=env,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def g_int_chain(tmp: Path) -> None:
    """End-to-end dry run: MockTransport request -> write_snapshot chain (FR-2/FR-4)."""
    request_url = "https://api.test/works?api_key=SECRET&mailto=a@b.c&filter=x"

    def handler(req):
        return httpx.Response(200, json={"meta": {"count": 2}})

    session = fake_client(handler)
    response = session.get(request_url)
    assert response.status_code == 200
    snap = _common.write_snapshot(
        [{"id": 1}, {"id": 2}], "demo", "filter=x", url=str(response.url), base_dir=tmp
    )
    manifest = _manifest_for(tmp, snap)
    assert manifest["url"] == "https://api.test/works?api_key=***&mailto=***&filter=x", manifest["url"]
    assert manifest["record_count"] == 2

    # Two same-day pulls coexist in one date dir with distinct stems + manifests.
    snap2 = _common.write_snapshot([{"id": 3}], "demo", "q2", base_dir=tmp, name="demo-second")
    assert snap2.parent == snap.parent
    names = sorted(p.name for p in snap.parent.iterdir())
    assert len(names) == 4 and all(n.endswith((".jsonl", ".manifest.json")) for n in names), names


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("G-1", lambda tmp: g1_layout(tmp)),
        ("G-2", lambda tmp: g2_formats(tmp)),
        ("G-3", lambda tmp: g3_manifest_fields(tmp)),
        ("G-4", lambda tmp: g4_sha_and_no_overwrite(tmp)),
        ("G-5", lambda _tmp: g5_retry_policy()),
        ("G-6", lambda _tmp: g6_session_env_wiring()),
        ("G-7", lambda tmp: g7_url_redaction(tmp)),
        ("G-8", lambda tmp: g8_type_error_and_import_guard(tmp)),
        ("G-INT", lambda tmp: g_int_chain(tmp)),
    ):
        with tempfile.TemporaryDirectory(prefix="check_common_") as td:
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
