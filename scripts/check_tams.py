"""TAM ≤ context gate for data/manual/assumptions.csv (issue-56, D-24).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_tams.py

Prints `G-<n> PASS|FAIL` per gate (G-1…G-8 unit gates over tmp fixtures +
G-INT integration over the REAL committed assumptions.csv v2) and exits
non-zero if any gate fails. Zero network access; every unit-gate write goes
into a per-gate tempfile.TemporaryDirectory dir; G-INT is read-only and
asserts the real data/processed/ tree is untouched. Imports ONLY
size_markets (self-contained scripts ruling, decisions.md 2026-09-29;
mirrors check_markets.py:41), reusing sm.load_package_model +
sm.read_assumptions so the locked v2 validation (header, strict
low < mid < high, EUR rule, layer shapes) runs BEFORE the D-24 rule.

The rule (D-24, decisions.md 2026-10-06 "D-24 adoption" block, Option A):
for every package that has both a `tam` layer and a `context` row in
assumptions.csv, the bound-wise TAM ceiling high(count) × high(monetary)
must be ≤ the context row's `high`; equality passes. Packages without a
context row are skipped — D-24 is conditional and absent layers are never
invented. The gate reads assumptions.csv directly, never data/processed/,
so it runs green/red on a fresh clone with zero pipeline steps.

Fixture packages are `zeta_pkg`/`alpha_pkg` — config order deliberately
DIFFERS from alphabetical so any accidental lexical sort fails the gates.
The six real package keys appear nowhere in this file's fixtures; only
G-INT references committed-data names, read through the real config.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import size_markets as sm  # noqa: E402

FIXTURE_PACKAGES = ("zeta_pkg", "alpha_pkg")  # config order != alphabetical


# --- fixture plumbing (mirrors check_markets.py write helpers) ----------------


def write_packages_yaml(tmp: Path) -> Path:
    cfg = tmp / "packages.yaml"
    cfg.write_text(
        "packages:\n"
        "  - name: zeta_pkg\n"
        "    display_name: Zeta Package\n"
        "    depth: deep\n"
        "  - name: alpha_pkg\n"
        "    display_name: Alpha Package\n"
        "    depth: light\n",
        encoding="utf-8",
    )
    return cfg


def write_assumptions(tmp: Path, rows: list[tuple[str, ...]],
                      header: list[str] | None = None, name: str = "assumptions.csv") -> Path:
    path = tmp / name
    lines = [",".join(header if header is not None else sm.ASSUMPTIONS_HEADER)]
    lines += [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _r(rid: int, pkg: str, layer: str, param: str, low: int, mid: int, high: int,
       unit: str, flag: str = "n") -> tuple[str, ...]:
    """One fixture row with the locked 11-column shape and 'fixture' source."""
    return (str(rid), pkg, layer, param, str(low), str(mid), str(high), unit,
            "fixture", "low", flag)


def _som(pkg: str, id0: int) -> list[tuple[str, ...]]:
    """The two som rows read_assumptions requires for every fixture package."""
    return [_r(id0, pkg, "som", "paying_profiles", 5, 10, 20, "profiles", "y"),
            _r(id0 + 1, pkg, "som", "price_per_profile", 100, 150, 200, "EUR/profile/year")]


def _violations_for(tmp: Path, rows: list[tuple[str, ...]]) -> list[str]:
    """Run the locked v2 validation, then the D-24 rule over fixture rows."""
    cfg = write_packages_yaml(tmp)
    packages = [name for name, _display_name, _depth in sm.load_package_model(cfg)]
    a = write_assumptions(tmp, rows, name="a.csv")
    return tam_context_violations(sm.read_assumptions(a, packages), packages)


def _sha_map(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def _expect_valueerror(label: str, fn, needle: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert needle in str(exc), f"{label}: expected {needle!r} in {exc!r}"
    else:
        raise AssertionError(f"{label}: expected ValueError naming {needle!r}")


# --- the D-24 rule -------------------------------------------------------------


def tam_context_violations(df: pd.DataFrame, packages: list[str]) -> list[str]:
    """D-24 Option A: high(count) × high(monetary) ≤ context.high, per package.

    Returns one actionable message per violating package (aggregation, not
    fail-fast). Packages missing a `tam` layer or a `context` row are skipped
    — the rule is conditional, absent layers are never invented. Row-shape
    guarantees (exactly one count + one monetary tam row, at most one context
    row) come from sm.read_assumptions, which must have run first. Products
    are Python arbitrary-precision ints — no float formatting in messages.
    """
    violations: list[str] = []
    for pkg in packages:
        sub = df[df["package"] == pkg]
        tam = sub[sub["layer"] == "tam"]
        ctx = sub[sub["layer"] == "context"]
        if tam.empty or ctx.empty:
            continue
        tam_rows = list(tam.itertuples(index=False))
        counts = [row for row in tam_rows if str(row.parameter) in sm.COUNT_PARAMS]
        money = [row for row in tam_rows if str(row.parameter) not in sm.COUNT_PARAMS]
        assert len(counts) == 1 and len(money) == 1, \
            f"{pkg}: read_assumptions guarantees one count × one monetary tam row"
        ctx_row = next(ctx.itertuples(index=False))
        count_high = int(counts[0].high)
        money_high = int(money[0].high)
        product = count_high * money_high
        if product > int(ctx_row.high):
            violations.append(
                f"package {pkg}: TAM envelope_high {count_high} × {money_high} = {product} "
                f"(rows id={counts[0].id} {counts[0].parameter}, "
                f"id={money[0].id} {money[0].parameter}) exceeds "
                f"context.high {ctx_row.high} (row id={ctx_row.id}) — "
                f"D-24 requires TAM ≤ context"
            )
    return violations


# --- unit gates ----------------------------------------------------------------


def g1_pass_case(tmp: Path) -> None:
    """U1: fixture TAM envelope_high (30×300=9000) < context.high (10000) -> no violation."""
    rows = [_r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 10000, "EUR/year"),
            _r(2, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs"),
            _r(3, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year"),
            *_som("zeta_pkg", 4),
            _r(6, "alpha_pkg", "som", "seats_per_year", 10, 20, 30, "seats", "y"),
            _r(7, "alpha_pkg", "som", "price_per_seat", 100, 200, 300, "EUR/seat")]
    assert _violations_for(tmp, rows) == []


def g2_fail_case(tmp: Path) -> None:
    """U2: envelope_high (9000) > context.high (8000) -> FAIL naming package,
    both products, context high and all three row ids."""
    rows = [_r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 8000, "EUR/year"),
            _r(2, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs"),
            _r(3, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year"),
            *_som("zeta_pkg", 4)]
    violations = _violations_for(tmp, rows)
    assert len(violations) == 1, violations
    msg = violations[0]
    for needle in ("zeta_pkg", "30 × 300 = 9000", "context.high 8000",
                   "id=1", "id=2", "id=3", "D-24"):
        assert needle in msg, f"{needle!r} missing from {msg!r}"


def g3_no_context_skip(tmp: Path) -> None:
    """U3: tam layer present, no context row -> skipped, no error (D-24 conditional)."""
    rows = [_r(1, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs"),
            _r(2, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year"),
            *_som("zeta_pkg", 3)]
    assert _violations_for(tmp, rows) == []


def g4_equality_boundary(tmp: Path) -> None:
    """U4: envelope_high == context.high (9000 == 9000) -> PASS (rule is ≤)."""
    rows = [_r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 9000, "EUR/year"),
            _r(2, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs"),
            _r(3, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year"),
            *_som("zeta_pkg", 4)]
    assert _violations_for(tmp, rows) == []


def g5_multi_violation_aggregation(tmp: Path) -> None:
    """U5: two violating packages in one fixture -> both reported, config order.

    X4 guard: the violating zeta count row is y-flagged (like the real p3/p5
    rows) — the messages must not depend on or alter flags.
    """
    rows = [_r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 8000, "EUR/year"),
            _r(2, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs", "y"),
            _r(3, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year"),
            *_som("zeta_pkg", 4),
            _r(6, "alpha_pkg", "context", "addressable_market_annual", 1000, 2000, 3000, "EUR/year"),
            _r(7, "alpha_pkg", "tam", "model_labs", 10, 20, 30, "labs"),
            _r(8, "alpha_pkg", "tam", "price_per_learner", 100, 200, 300, "EUR/learner"),
            _r(9, "alpha_pkg", "som", "seats_per_year", 10, 20, 30, "seats", "y"),
            _r(10, "alpha_pkg", "som", "price_per_seat", 100, 200, 300, "EUR/seat")]
    violations = _violations_for(tmp, rows)
    assert len(violations) == 2, violations
    assert violations[0].startswith("package zeta_pkg"), violations[0]
    assert violations[1].startswith("package alpha_pkg"), violations[1]
    # flag-insensitivity: identical message with the count row n-flagged
    rows_unflagged = [r[:10] + ("n",) if i == 1 else r for i, r in enumerate(rows)]
    assert _violations_for(tmp, rows_unflagged) == violations, "flags must not alter messages"


def g6_context_without_tam_skip(tmp: Path) -> None:
    """U6/X1: context row present, no tam row -> skipped (vacuous)."""
    rows = [_r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 8000, "EUR/year"),
            *_som("zeta_pkg", 2)]
    assert _violations_for(tmp, rows) == []


def g7_upstream_validation(tmp: Path) -> None:
    """U7/X3: malformed fixtures raise sm.read_assumptions ValueError BEFORE the rule."""
    base_ok = [_r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 8000, "EUR/year"),
               _r(2, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs"),
               _r(3, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year"),
               *_som("zeta_pkg", 4)]
    cfg = write_packages_yaml(tmp)
    packages = [name for name, _display_name, _depth in sm.load_package_model(cfg)]

    bad_header = write_assumptions(tmp, base_ok, header=sm.ASSUMPTIONS_HEADER[1:] +
                                   sm.ASSUMPTIONS_HEADER[:1], name="h.csv")
    _expect_valueerror("header", lambda: sm.read_assumptions(bad_header, packages), "header")

    nonint = [base_ok[0], base_ok[1][:4] + ("ten",) + base_ok[1][5:], *base_ok[2:]]
    a = write_assumptions(tmp, nonint, name="ni.csv")
    _expect_valueerror("non-integer", lambda: sm.read_assumptions(a, packages), "non-integer")

    inverted = [base_ok[0], base_ok[1][:4] + ("30", "20", "10") + base_ok[1][7:], *base_ok[2:]]
    a = write_assumptions(tmp, inverted, name="inv.csv")
    _expect_valueerror("inverted band", lambda: sm.read_assumptions(a, packages), "low < mid < high")

    tam_three = [*base_ok[:3], _r(9, "zeta_pkg", "tam", "model_labs", 1, 2, 3, "labs"), *base_ok[3:]]
    a = write_assumptions(tmp, tam_three, name="tam3.csv")
    _expect_valueerror("tam 3 rows", lambda: sm.read_assumptions(a, packages), "tam")


def g8_order_independence(tmp: Path) -> None:
    """U8: monetary row before count row in the file -> identical verdict (product commutes)."""
    violating_ctx = _r(1, "zeta_pkg", "context", "addressable_market_annual", 2000, 5000, 8000, "EUR/year")
    count_row = _r(2, "zeta_pkg", "tam", "active_labs", 10, 20, 30, "labs")
    money_row = _r(3, "zeta_pkg", "tam", "spend_per_lab_per_year", 100, 200, 300, "EUR/lab/year")
    som_rows = _som("zeta_pkg", 4)
    count_first = _violations_for(tmp, [violating_ctx, count_row, money_row, *som_rows])
    money_first = _violations_for(tmp, [violating_ctx, money_row, count_row, *som_rows])
    assert len(count_first) == 1 and count_first == money_first, (count_first, money_first)


def g_int_real_data(tmp: Path) -> None:
    """G-INT: the D-24 rule over the REAL committed assumptions.csv (read-only).

    Must PASS after the issue-56 TAM rebuild; on pre-rebuild data it fails
    with exactly the three violations p3/p4/p5 (the acceptance demo, I5).
    Asserts the data/processed/ tree is untouched — the gate never runs the
    pipeline and has no `make size` ordering dependency (E1).
    """
    processed = REPO_ROOT / "data" / "processed"
    before = _sha_map(processed) if processed.is_dir() else {}
    model = sm.load_package_model(sm.PACKAGES_PATH)
    packages = [name for name, _display_name, _depth in model]
    df = sm.read_assumptions(sm.ASSUMPTIONS_PATH, packages)
    after = _sha_map(processed) if processed.is_dir() else {}
    assert before == after, "G-INT must not touch the real data/processed/ tree"
    violations = tam_context_violations(df, packages)
    assert not violations, "D-24 TAM ≤ context violations:\n" + "\n".join(violations)


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("G-1", g1_pass_case),
        ("G-2", g2_fail_case),
        ("G-3", g3_no_context_skip),
        ("G-4", g4_equality_boundary),
        ("G-5", g5_multi_violation_aggregation),
        ("G-6", g6_context_without_tam_skip),
        ("G-7", g7_upstream_validation),
        ("G-8", g8_order_independence),
        ("G-INT", g_int_real_data),
    ):
        with tempfile.TemporaryDirectory(prefix="check_tams_") as td:
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
