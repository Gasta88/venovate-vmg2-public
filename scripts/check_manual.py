"""Ad-hoc verification gates for the four manual evidence CSVs (issue export-audit;
platform referral band added by issue 57).

The repo has no test runner, so this committed gate script substitutes for one.
Run:

    uv run python scripts/check_manual.py

Prints `G-<n> PASS|FAIL` per gate (G-0…G-3 + G-INT integration over the REAL
committed manual CSVs) and exits non-zero if any gate fails. Zero network
access; every write goes into per-gate tempfile.TemporaryDirectory dirs —
G-INT writes only to its tmp dir and asserts the real data/manual/ tree is
untouched. Mirrors check_markets.py's runner and fixture style.

Four files gated (schemas differ — per-file rules):
- data/manual/competitors.csv        (8 cols, 18-value package vocabulary)
- data/manual/training_offers.csv    (9 cols v2 — issue-53 D-15/WI-04 widened
  the locked 6-col schema with days/price_eur/price_per_day_eur for
  price-per-day normalisation; the original 6 columns keep exact order and
  verbatim content; still no checked_on — that rule is NOT superseded)
- data/manual/assumptions.csv        (10 cols, per-package TAM/SOM bands)
- data/manual/platform.csv           (8 cols, exactly one referral_rate_pct
  row — G-3; band strictly low < mid < high, confidence=low and
  test_in_validation=n pinned per the issue-57 ruling: the eight-row
  viability set is locked by D-14 and the referral probe lives in the
  Annex 2 open items instead)
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "manual"

# --- constants ----------------------------------------------------------------

COMPETITORS_HEADER = [
    "package", "name", "type", "offer", "public_price",
    "source_url", "checked_on", "confidence",
]

COMPETITORS_PACKAGES = frozenset({
    "peptide_chemistry_cro", "toxin_specialist", "validation_as_a_service",
    "ai_bio_synthesis_validation", "freelance_scientist_platform",
    "science_service_marketplace", "expert_network", "direct_sourcing",
    "bioinformatics_training_provider", "md_comp_chem_school",
    "hpc_training_network", "free_self_serve_tutorial",
    "spatial_omics_vendor", "spatial_omics_service_provider",
    "spatial_omics_core_facility", "spatial_omics_inhouse_pipeline",
    "venomate_substitute", "invertebrate_model_supplier",
})

TRAINING_HEADER = ["provider", "course", "year", "price", "format", "source_url",
                   "days", "price_eur", "price_per_day_eur"]

# Normalisation convention for the three appended analyst columns (v2,
# decisions.md 2026-10-05 D-15 supersession of the 6-col clause): reference
# tier = academic/student where the price cell lists tiers; conversion rates
# CHF->EUR 0.93, GBP->EUR 1.16, USD->EUR at ECB 1.1367 (2026-09-24, the repo
# convention); "~2 weeks" teaching schools normalised to 10 days; days empty
# where no per-day rate is defensible (online/self-paced/async or duration
# not stated); price_eur empty where no fee is published.
TRAINING_NUMERIC_COLS = ("days", "price_eur", "price_per_day_eur")

# Platform referral band (issue 57 WI-16): locked 8-col schema, exactly one
# referral_rate_pct row. Mirrors size_markets.PLATFORM_HEADER — declared
# independently so drift in one copy does not silently pass the other's
# checks (the same self-contained duplication ruling as the other gates).
PLATFORM_HEADER = ["field", "low", "mid", "high", "unit", "source",
                   "confidence", "test_in_validation"]
PLATFORM_FIELD = "referral_rate_pct"

CONFIDENCE_VALUES = frozenset({"high", "moderate", "low"})
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_RE = re.compile(r"^\d{4}$")
HTTP_RE = re.compile(r"^https?://")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV, skipping comment lines starting with #."""
    text = path.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    return list(reader)


def _write_csv(tmp: Path, name: str, header: list[str],
               rows: list[tuple[str, ...]], comment: str | None = None) -> Path:
    p = tmp / name
    lines: list[str] = []
    if comment:
        lines.append(comment)
    lines.append(",".join(header))
    lines += [",".join(r) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _sha_map(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


# --- G-0: source scan ---------------------------------------------------------

def g0_no_forbidden_imports(tmp: Path) -> None:
    """AC-11/DoD-6: check_manual.py must NOT import _common or HTTP-client libs."""
    import ast
    content = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "_common" and not alias.name.endswith("._common"), \
                    "check_manual.py must NOT import _common"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "_common" and not (node.module or "").endswith("._common"), \
                "check_manual.py must NOT import from _common"
    for banned in ("httpx", "requests", "urllib", "socket"):
        # Allow the word inside comments/docstrings only if not an actual import
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if f"import {banned}" in line or f"from {banned}" in line:
                raise AssertionError(f"check_manual.py must not import {banned!r} (offline)")


# --- G-1: competitors.csv -----------------------------------------------------

def _assert_competitors(path: Path) -> None:
    rows = _read_csv_rows(path)
    # Header check via DictReader fieldnames
    text = path.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    assert lines[0] == ",".join(COMPETITORS_HEADER), \
        f"competitors.csv header drift: {lines[0]!r}"
    for i, row in enumerate(rows):
        vals = list(row.values())
        assert len(vals) == 8, f"row {i}: expected 8 fields, got {len(vals)}"
        pkg = row.get("package", "")
        assert pkg in COMPETITORS_PACKAGES, f"row {i}: package {pkg!r} not in vocabulary"
        src = row.get("source_url", "")
        assert HTTP_RE.match(src), f"row {i}: source_url {src!r} not a valid URL"
        co = row.get("checked_on", "")
        assert DATE_RE.match(co), f"row {i}: checked_on {co!r} not YYYY-MM-DD"
        conf = row.get("confidence", "")
        assert conf in CONFIDENCE_VALUES, f"row {i}: confidence {conf!r} not in {CONFIDENCE_VALUES}"


def g1_competitors(tmp: Path) -> None:
    _assert_competitors(DATA_DIR / "competitors.csv")
    # Positive fixture
    p = _write_csv(tmp, "competitors.csv", COMPETITORS_HEADER, [
        ("peptide_chemistry_cro", "TestCorp", "CRO", "SPPS", "$1/AA",
         "https://example.com", "2026-09-30", "high"),
    ])
    _assert_competitors(p)
    # Negative: bad package
    bad = _write_csv(tmp, "bad_pkg.csv", COMPETITORS_HEADER, [
        ("unknown_role", "X", "CRO", "SPPS", "$1", "https://x.com", "2026-01-01", "high"),
    ])
    try:
        _assert_competitors(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-1 should fail on unknown package")
    # Negative: empty source_url
    bad2 = _write_csv(tmp, "bad_src.csv", COMPETITORS_HEADER, [
        ("peptide_chemistry_cro", "X", "CRO", "SPPS", "$1", "", "2026-01-01", "high"),
    ])
    try:
        _assert_competitors(bad2)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-1 should fail on empty source_url")
    # Negative: malformed checked_on
    bad3 = _write_csv(tmp, "bad_date.csv", COMPETITORS_HEADER, [
        ("peptide_chemistry_cro", "X", "CRO", "SPPS", "$1", "https://x.com", "2026-9-3", "high"),
    ])
    try:
        _assert_competitors(bad3)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-1 should fail on malformed checked_on")
    # Negative: bad confidence
    bad4 = _write_csv(tmp, "bad_conf.csv", COMPETITORS_HEADER, [
        ("peptide_chemistry_cro", "X", "CRO", "SPPS", "$1", "https://x.com", "2026-01-01", "med"),
    ])
    try:
        _assert_competitors(bad4)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-1 should fail on bad confidence value")


# --- G-2: training_offers.csv -------------------------------------------------

def _assert_training(path: Path) -> None:
    """v2 rules (issue-53 D-15/WI-04): exact 9-col header, per-day arithmetic
    within rounding tolerance, numeric-or-empty normalisation columns,
    source_url required per row, no checked_on (that clause is NOT superseded)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == ",".join(TRAINING_HEADER), \
        f"training_offers.csv header drift: {lines[0]!r}"
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    for i, row in enumerate(rows):
        vals = list(row.values())
        assert len(vals) == 9, f"row {i}: expected 9 fields, got {len(vals)}"
        src = row.get("source_url", "")
        assert HTTP_RE.match(src), f"row {i}: source_url {src!r} not a valid URL"
        yr = row.get("year", "")
        assert YEAR_RE.match(yr), f"row {i}: year {yr!r} not YYYY"
        nums: dict[str, float | None] = {}
        for col in TRAINING_NUMERIC_COLS:
            cell = (row.get(col) or "").strip()
            if cell == "":
                nums[col] = None
                continue
            try:
                nums[col] = float(cell)
            except ValueError:
                raise AssertionError(f"row {i}: {col} {cell!r} not numeric-or-empty") from None
        days, price, per_day = nums["days"], nums["price_eur"], nums["price_per_day_eur"]
        if days is not None and price is not None:
            assert per_day is not None, f"row {i}: days+price_eur set but price_per_day_eur empty"
            assert abs(price / days - per_day) <= 0.01, \
                f"row {i}: price_per_day_eur {per_day} != price_eur/days {price}/{days} (tol 0.01)"
        else:
            assert per_day is None, \
                f"row {i}: price_per_day_eur set while days/price_eur empty (not derivable)"
    # training_offers must NOT have checked_on (kept — not superseded by D-15)
    assert "checked_on" not in (lines[0].split(",")), \
        "training_offers.csv must NOT have checked_on column (kept rule, v2 widening aside)"


def g2_training(tmp: Path) -> None:
    _assert_training(DATA_DIR / "training_offers.csv")
    # Positive fixture (v2 9-col)
    p = _write_csv(tmp, "training.csv", TRAINING_HEADER, [
        ("TestOrg", "TestCourse", "2026", "EUR 100", "online; 2 days", "https://example.com",
         "2", "100", "50.00"),
        ("FreeOrg", "SelfPaced", "2026", "Free", "Self-paced online", "https://example.org",
         "", "0", ""),
    ])
    _assert_training(p)
    # Negative: old 6-col header drift (superseded schema must fail)
    bad6 = _write_csv(tmp, "old6.csv", TRAINING_HEADER[:6], [
        ("X", "Y", "2026", "EUR 100", "online", "https://x.com"),
    ])
    try:
        _assert_training(bad6)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-2 should fail on the pre-v2 6-col header")
    # Negative: checked_on added (kept rule)
    bad = _write_csv(tmp, "bad_h.csv", TRAINING_HEADER + ["checked_on"], [
        ("X", "Y", "2026", "EUR 100", "online", "https://x.com", "2", "100", "50.00", "2026-01-01"),
    ])
    try:
        _assert_training(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-2 should fail on header drift (extra checked_on col)")
    # Negative: per-day arithmetic drift
    bad_math = _write_csv(tmp, "math.csv", TRAINING_HEADER, [
        ("X", "Y", "2026", "EUR 100", "2 days", "https://x.com", "2", "100", "60.00"),
    ])
    try:
        _assert_training(bad_math)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-2 should fail when price_per_day_eur != price_eur/days")
    # Negative: per-day set while days empty
    bad_derived = _write_csv(tmp, "derived.csv", TRAINING_HEADER, [
        ("X", "Y", "2026", "EUR 100", "self-paced", "https://x.com", "", "100", "10.00"),
    ])
    try:
        _assert_training(bad_derived)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-2 should fail when price_per_day_eur is set without days")


# --- G-3: platform.csv (issue-57 WI-16) --------------------------------------


def _assert_platform(path: Path) -> None:
    """Locked platform rules (issue-57): exact 8-col header, exactly one
    referral_rate_pct row, integer band strictly low < mid < high, non-empty
    unit and source, confidence=low and test_in_validation=n pinned — the
    eight-row viability set is locked by D-14 (the referral probe lives in
    the Annex 2 open items), and the band is an analyst ceiling at low
    confidence (D-25/WI-17 scope-and-limitations)."""
    text = path.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    assert lines[0] == ",".join(PLATFORM_HEADER), \
        f"platform.csv header drift: {lines[0]!r}"
    rows = _read_csv_rows(path)
    assert len(rows) == 1, f"platform.csv: expected exactly 1 row, got {len(rows)}"
    row = rows[0]
    vals = list(row.values())
    assert len(vals) == 8, f"expected 8 fields, got {len(vals)}"
    assert row.get("field", "") == PLATFORM_FIELD, \
        f"field {row.get('field', '')!r} is not {PLATFORM_FIELD!r}"
    bounds: dict[str, int] = {}
    for col in ("low", "mid", "high"):
        cell = (row.get(col) or "").strip()
        try:
            bounds[col] = int(cell)
        except ValueError:
            raise AssertionError(f"platform.csv: {col} {cell!r} not an integer") from None
    assert bounds["low"] < bounds["mid"] < bounds["high"], \
        f"platform.csv: band must be strictly low < mid < high, got " \
        f"{row['low']} / {row['mid']} / {row['high']}"
    assert (row.get("unit") or "").strip(), "platform.csv: unit must not be empty"
    assert len((row.get("source") or "").strip()) > 40, \
        "platform.csv: the referral row needs its written basis (dated source text)"
    assert row.get("confidence", "") == "low", \
        f"platform.csv: confidence must be 'low' (analyst ceiling, issue-57), " \
        f"got {row.get('confidence', '')!r}"
    assert row.get("test_in_validation", "") == "n", \
        "platform.csv: test_in_validation must stay 'n' — the eight-row " \
        "viability set is locked (D-14); the referral probe is an Annex 2 " \
        "open item"


def g3_platform(tmp: Path) -> None:
    _assert_platform(DATA_DIR / "platform.csv")
    # Positive fixture
    good = _write_csv(tmp, "platform.csv", PLATFORM_HEADER, [
        (PLATFORM_FIELD, "3", "4", "5", "percent of routed work",
         "Fixture basis text long enough to pass the written-basis rule",
         "low", "n"),
    ])
    _assert_platform(good)
    # Negative: header drift
    bad_h = _write_csv(tmp, "h.csv", PLATFORM_HEADER[:7], [
        (PLATFORM_FIELD, "3", "4", "5", "u", "basis basis basis basis basis long", "low"),
    ])
    try:
        _assert_platform(bad_h)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-3 should fail on header drift")
    # Negative: two rows
    bad_n = _write_csv(tmp, "n.csv", PLATFORM_HEADER, [
        (PLATFORM_FIELD, "3", "4", "5", "u", "basis basis basis basis basis long", "low", "n"),
        (PLATFORM_FIELD, "3", "4", "5", "u", "basis basis basis basis basis long", "low", "n"),
    ])
    try:
        _assert_platform(bad_n)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-3 should fail on a second row")
    # Negative: wrong field name
    bad_f = _write_csv(tmp, "f.csv", PLATFORM_HEADER, [
        ("commission_pct", "3", "4", "5", "u", "basis basis basis basis basis long", "low", "n"),
    ])
    try:
        _assert_platform(bad_f)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-3 should fail on a wrong field name")
    # Negative: flat band (point estimate)
    bad_b = _write_csv(tmp, "b.csv", PLATFORM_HEADER, [
        (PLATFORM_FIELD, "4", "4", "5", "u", "basis basis basis basis basis long", "low", "n"),
    ])
    try:
        _assert_platform(bad_b)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-3 should fail on a non-strict band")
    # Negative: test_in_validation flipped to y (D-14 lock)
    bad_v = _write_csv(tmp, "v.csv", PLATFORM_HEADER, [
        (PLATFORM_FIELD, "3", "4", "5", "u", "basis basis basis basis basis long", "low", "y"),
    ])
    try:
        _assert_platform(bad_v)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-3 should fail when test_in_validation is y")
    # Negative: confidence raised above low (analyst-ceiling ruling)
    bad_c = _write_csv(tmp, "c.csv", PLATFORM_HEADER, [
        (PLATFORM_FIELD, "3", "4", "5", "u", "basis basis basis basis basis long", "high", "n"),
    ])
    try:
        _assert_platform(bad_c)
    except AssertionError:
        pass
    else:
        raise AssertionError("G-3 should fail on confidence != low")


# --- G-INT: real-data smoke ---------------------------------------------------

def g_int_real_data(tmp: Path) -> None:
    """Offline smoke over the REAL committed manual CSVs; real tree untouched."""
    before = _sha_map(DATA_DIR)

    # All four files pass their gates
    _assert_competitors(DATA_DIR / "competitors.csv")
    _assert_training(DATA_DIR / "training_offers.csv")
    _assert_platform(DATA_DIR / "platform.csv")

    after = _sha_map(DATA_DIR)
    assert before == after, "G-INT must not touch the real data/manual/ tree"

    # Minimum row counts (minimums, not pins — competitors grows)
    comp_rows = _read_csv_rows(DATA_DIR / "competitors.csv")
    assert len(comp_rows) >= 63, f"competitors.csv: {len(comp_rows)} rows < 63 minimum"
    train_rows = _read_csv_rows(DATA_DIR / "training_offers.csv")
    assert len(train_rows) >= 19, f"training_offers.csv: {len(train_rows)} rows < 19 minimum"
    # platform.csv is pinned at exactly one row by its schema contract.
    plat_rows = _read_csv_rows(DATA_DIR / "platform.csv")
    assert len(plat_rows) == 1, f"platform.csv: {len(plat_rows)} rows != 1"

    # Determinism: two consecutive runs produce identical output (we re-assert)
    _assert_competitors(DATA_DIR / "competitors.csv")
    _assert_training(DATA_DIR / "training_offers.csv")
    _assert_platform(DATA_DIR / "platform.csv")


def main(data_dir: Path | None = None) -> int:
    d = data_dir or DATA_DIR
    # Override DATA_DIR for fixture gates when data_dir is passed
    failures = 0
    for gate_id, fn in (
        ("G-0", g0_no_forbidden_imports),
        ("G-1", g1_competitors),
        ("G-2", g2_training),
        ("G-3", g3_platform),
        ("G-INT", g_int_real_data),
    ):
        with tempfile.TemporaryDirectory(prefix="check_manual_") as td:
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
