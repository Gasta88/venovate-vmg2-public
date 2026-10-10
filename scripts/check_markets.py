"""Ad-hoc verification gates for scripts/size_markets.py (issue-10, v2 for issue-53).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_markets.py

Prints `G-<n> PASS|FAIL` per gate (G-0…G-8 + G-INT integration over the REAL
committed assumptions.csv v2 + packages.yaml) and exits non-zero if any gate
fails. Zero network access; every write goes into per-gate
tempfile.TemporaryDirectory dirs — G-INT writes only to its tmp dir and
asserts the real data/processed/ tree is untouched. Mirrors
check_tables.py's runner and fixture style; imports ONLY size_markets
(self-contained scripts ruling, decisions.md 2026-09-29).

Fixture packages are `zeta_pkg`/`alpha_pkg` — config order deliberately
DIFFERS from alphabetical so any accidental lexical sort fails the gates.
The six real package keys appear nowhere in this file's fixtures; only
G-INT references committed-data names, read through the real config.

v2 contract (issue-53 D-04/D-05/D-14): 11-column assumptions header with a
`layer` column; layers context (1 row) / tam (2 rows) / som (2 rows,
required); independent triangular Monte Carlo (fixed seed, 10,000 draws,
P10/P50/P90) with bound-wise products kept only as envelope columns; the
validation set is the 8 viability assumptions (5–8 band rule kept).

Platform contract (issue-57 WI-16, G-8 + G-INT): the platform revenue step
runs strictly AFTER the full package loop (the 15 market_sizing rows stay
byte-identical with the step enabled — asserted by BASELINE in G-INT);
platform.csv's one referral_rate_pct band is validated loudly; the
profiles/referral/total rows of platform_revenue.csv are deterministic under
MC_SEED, monotonic, and the profiles row equals the p5 som row (reused
column); G-8 proves the rng-order guarantee and the draw-wise total identity
element-wise on a small synthetic band.
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import size_markets as sm  # noqa: E402

FIXTURE_PACKAGES = ("zeta_pkg", "alpha_pkg")  # config order != alphabetical

# Hand-computed fixture math: tam envelope 10x100=1000 low, 30x300=9000 high;
# context band passes through its own MC; som envelope 5x100=500 low,
# 20x200=4000 high. Light fixture alpha_pkg carries ONLY a som row (edge case:
# light package with only a som row; absent layers emit no row, never zeros).
ZETA_CONTEXT = ("1", "zeta_pkg", "context", "addressable_market_annual", "2000", "5000", "8000",
                "EUR/year", "fixture", "low", "n")
ZETA_LABS = ("2", "zeta_pkg", "tam", "active_labs", "10", "20", "30", "labs", "fixture", "low", "y")
ZETA_SPEND = ("3", "zeta_pkg", "tam", "spend_per_lab_per_year", "100", "200", "300",
              "EUR/lab/year", "fixture", "low", "n")
ZETA_PROFILES = ("4", "zeta_pkg", "som", "paying_profiles", "5", "10", "20",
                 "profiles", "fixture", "low", "y")
ZETA_PRICE = ("5", "zeta_pkg", "som", "price_per_profile", "100", "150", "200",
              "EUR/profile/year", "fixture", "low", "n")
ALPHA_SEATS = ("6", "alpha_pkg", "som", "seats_per_year", "10", "20", "30",
               "seats", "fixture", "low", "y")
ALPHA_PRICE = ("7", "alpha_pkg", "som", "price_per_seat", "100", "200", "300",
               "EUR/seat", "fixture", "low", "n")
FIXTURE_ROWS = [ZETA_CONTEXT, ZETA_LABS, ZETA_SPEND, ZETA_PROFILES, ZETA_PRICE,
                ALPHA_SEATS, ALPHA_PRICE]

# Committed-data baseline (v2 bytes, fixed seed sm.MC_SEED, 10,000 draws).
# Regenerated 2026-10-06 for issue-57 WI-15: som rows p1/p2/p3/p4 follow the
# basis-aligned capacity bands (assumptions.csv ids 3/7/12/17); the other 11
# rows are byte-identical to the pre-issue-57 baseline.
BASELINE = (
    "package,layer,p10,p50,p90,envelope_low,envelope_high,unit,test_in_validation\n"
    "p1_venospatial,tam,94112351,194011245,347121846,15000000,720000000,EUR/year,n\n"
    "p1_venospatial,som,140496,225680,346371,50000,612500,EUR/year,y\n"
    "p2_insilico_workshop,tam,6858588,15587715,32487896,900000,75000000,EUR/year,n\n"
    "p2_insilico_workshop,som,8961,13002,18149,4800,26400,EUR/year,y\n"
    "p3_marketplace,context,105934151,174149525,243185472,50000000,300000000,EUR/year,n\n"
    "p3_marketplace,tam,60516530,106321456,168335266,15000000,288000000,EUR/year,y\n"
    "p3_marketplace,som,126409,256241,477514,24000,960000,EUR/year,y\n"
    "p4_peptide_service,context,167682476,250453926,333669455,100000000,400000000,EUR/year,n\n"
    "p4_peptide_service,tam,46892795,96291709,163326674,6000000,320000000,EUR/year,n\n"
    "p4_peptide_service,som,286085,373090,474963,168000,672000,EUR/year,y\n"
    "p5_venomate,context,10743534,18886078,30687830,5000000,40000000,EUR/year,n\n"
    "p5_venomate,tam,613616,1281571,2288543,80000,4800000,EUR/year,y\n"
    "p5_venomate,som,8144,13638,22055,3200,40000,EUR/year,y\n"
    "p6_gem,tam,432162,743444,1170314,160000,1890000,EUR/year,n\n"
    "p6_gem,som,38466,57775,84738,19200,129600,EUR/year,y\n"
)

# Committed platform-revenue baseline (issue-57 WI-16; same seed/draws).
# Regenerated 2026-10-06 with the WI-15 basis-aligned som columns; the
# profiles row is the p5 som row, referral = 3-5% of the routed sum of the
# other five som columns, total = draw-wise sum of the two (quantiles are
# not additive).
PLATFORM_BASELINE = (
    "component,p10,p50,p90\n"
    "profiles,8144,13638,22055\n"
    "referral,29011,37953,49573\n"
    "total,41371,52316,66025\n"
)


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


def fixture(tmp: Path) -> tuple[Path, Path]:
    """(assumptions.csv, packages.yaml) covering deep-3-layer + som-only shapes."""
    cfg = write_packages_yaml(tmp)
    a = write_assumptions(tmp, FIXTURE_ROWS)
    return a, cfg


def _names(cfg: Path) -> list[str]:
    return [name for name, _display_name, _depth in sm.load_package_model(cfg)]


def _sha_map(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def _expect_valueerror(label: str, fn, needle: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert needle in str(exc), f"{label}: expected {needle!r} in {exc!r}"
    else:
        raise AssertionError(f"{label}: expected ValueError naming {needle!r}")


def _run(tmp: Path, rows: list[tuple[str, ...]], name: str = "a.csv") -> pd.DataFrame:
    a = write_assumptions(tmp, rows, name=name)
    return sm.read_assumptions(a, ["zeta_pkg", "alpha_pkg"])


# --- unit gates: one per validation/derivation function ---------------------------------


def g0_no_common_import(tmp: Path) -> None:
    """AC11/DoD-8: size_markets.py must NOT import _common or call write_snapshot."""
    script = REPO_ROOT / "scripts" / "size_markets.py"
    content = script.read_text(encoding="utf-8")
    assert "import _common" not in content and "from _common" not in content, \
        "size_markets.py must NOT import _common (AC11)"
    assert "write_snapshot" not in content or "NOT reused" in content, \
        "size_markets.py must NOT call write_snapshot (AC11); only 'NOT reused' comment allowed"
    # DoD-8: stdlib (incl. random for the MC) + pandas + yaml only — no
    # network/env stack, no numpy/scipy (thin-scaffolding ruling).
    for banned in ("httpx", "requests", "dotenv", "os.environ", "socket", "numpy", "scipy"):
        assert banned not in content, f"size_markets.py must not touch {banned!r} (offline, no env reads, stdlib MC)"


def g1_package_model(tmp: Path) -> None:
    cfg = write_packages_yaml(tmp)
    model = sm.load_package_model(cfg)
    # config order preserved — NOT alphabetical (alpha_pkg would sort first)
    assert model == [("zeta_pkg", "Zeta Package", "deep"), ("alpha_pkg", "Alpha Package", "light")], model
    _expect_valueerror("empty", lambda: sm.load_package_model(
        _write(tmp, "empty.yaml", "segments: []\n")), "no package names")
    _expect_valueerror("missing depth", lambda: sm.load_package_model(_write(tmp, "d1.yaml",
        "packages:\n  - name: zeta_pkg\n")), "zeta_pkg")
    _expect_valueerror("missing name", lambda: sm.load_package_model(_write(tmp, "d2.yaml",
        "packages:\n  - depth: deep\n")), "missing `name`")
    _expect_valueerror("unknown depth", lambda: sm.load_package_model(_write(tmp, "d3.yaml",
        "packages:\n  - name: zeta_pkg\n    depth: medium\n")), "medium")


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def g2_read_assumptions(tmp: Path) -> None:
    a, cfg = fixture(tmp)
    names = _names(cfg)
    df = sm.read_assumptions(a, names)
    assert list(df.columns) == sm.ASSUMPTIONS_HEADER
    assert len(df) == 7
    assert all(str(t) == "str" for t in df.dtypes), dict(df.dtypes)  # dtype=str pattern

    reordered = sm.ASSUMPTIONS_HEADER[1:] + sm.ASSUMPTIONS_HEADER[:1]
    bad_header = write_assumptions(tmp, FIXTURE_ROWS, header=reordered, name="h.csv")
    _expect_valueerror("header", lambda: sm.read_assumptions(bad_header, names), "header")

    ghost = write_assumptions(tmp, FIXTURE_ROWS + [("8", "ghost_pkg", "som", "seats_per_year",
                                                    "1", "2", "3", "seats", "f", "low", "n"),
                                                   ("9", "ghost_pkg", "som", "price_per_seat",
                                                    "1", "2", "3", "EUR/seat", "f", "low", "n")],
                              name="ghost.csv")
    _expect_valueerror("unknown package", lambda: sm.read_assumptions(ghost, names), "ghost_pkg")

    bad_layer = write_assumptions(tmp, [ZETA_CONTEXT[:2] + ("market",) + ZETA_CONTEXT[3:]] +
                                  FIXTURE_ROWS[1:], name="layer.csv")
    _expect_valueerror("bad layer", lambda: sm.read_assumptions(bad_layer, names), "layer")

    nonint = write_assumptions(tmp, [ZETA_CONTEXT,
                                     ZETA_LABS[:4] + ("ten",) + ZETA_LABS[5:] ,
                                     ZETA_SPEND, ZETA_PROFILES, ZETA_PRICE,
                                     ALPHA_SEATS, ALPHA_PRICE], name="ni.csv")
    _expect_valueerror("non-integer", lambda: sm.read_assumptions(nonint, names), "non-integer")

    low_eq_mid = write_assumptions(tmp, [ZETA_CONTEXT,
                                         ZETA_LABS[:4] + ("20", "20", "30") + ZETA_LABS[7:],
                                         ZETA_SPEND, ZETA_PROFILES, ZETA_PRICE,
                                         ALPHA_SEATS, ALPHA_PRICE], name="le.csv")
    _expect_valueerror("low==mid", lambda: sm.read_assumptions(low_eq_mid, names), "low < mid < high")

    # point estimates stay forbidden at the CSV level even though the MC would
    # accept a degenerate band (edge case in the spec Test Strategy)
    degenerate = write_assumptions(tmp, [ZETA_CONTEXT,
                                         ZETA_LABS[:4] + ("20", "20", "20") + ZETA_LABS[7:],
                                         ZETA_SPEND, ZETA_PROFILES, ZETA_PRICE,
                                         ALPHA_SEATS, ALPHA_PRICE], name="degen.csv")
    _expect_valueerror("degenerate band", lambda: sm.read_assumptions(degenerate, names), "low < mid < high")

    usd = write_assumptions(tmp, [ZETA_CONTEXT, ZETA_LABS,
                                  ZETA_SPEND[:7] + ("USD/lab/year",) + ZETA_SPEND[8:],
                                  ZETA_PROFILES, ZETA_PRICE, ALPHA_SEATS, ALPHA_PRICE], name="usd.csv")
    _expect_valueerror("non-EUR unit", lambda: sm.read_assumptions(usd, names), "EUR")

    no_som = write_assumptions(tmp, [ZETA_CONTEXT, ZETA_LABS, ZETA_SPEND,
                                     ALPHA_SEATS, ALPHA_PRICE], name="nosom.csv")
    _expect_valueerror("missing som", lambda: sm.read_assumptions(no_som, names), "no `som` layer")

    tam_three = write_assumptions(tmp, [ZETA_CONTEXT, ZETA_LABS, ZETA_SPEND,
                                        ("8", "zeta_pkg", "tam", "model_labs", "1", "2", "3",
                                         "labs", "f", "low", "n"),
                                        ZETA_PROFILES, ZETA_PRICE, ALPHA_SEATS, ALPHA_PRICE],
                                  name="tam3.csv")
    _expect_valueerror("tam 3 rows", lambda: sm.read_assumptions(tam_three, names), "tam")

    two_rates = write_assumptions(tmp, [ZETA_CONTEXT, ZETA_LABS, ZETA_SPEND,
                                        ("8", "zeta_pkg", "som", "price_per_seat", "1", "2", "3",
                                         "EUR/seat", "f", "low", "n"),
                                        ZETA_PRICE, ALPHA_SEATS, ALPHA_PRICE], name="2rate.csv")
    _expect_valueerror("som two rates", lambda: sm.read_assumptions(two_rates, names), "one count")

    ctx_two = write_assumptions(tmp, [ZETA_CONTEXT,
                                      ("8", "zeta_pkg", "context", "addressable_market_annual",
                                       "1", "2", "3", "EUR/year", "f", "low", "n"),
                                      ZETA_LABS, ZETA_SPEND, ZETA_PROFILES, ZETA_PRICE,
                                      ALPHA_SEATS, ALPHA_PRICE], name="ctx2.csv")
    _expect_valueerror("context 2 rows", lambda: sm.read_assumptions(ctx_two, names), "context")


def g3_simulate_layer(tmp: Path) -> None:
    """MC core: quantile order, envelope = bound-wise products, degenerate sanity."""
    rng = random.Random(sm.MC_SEED)
    sized = sm.simulate_layer([(10, 20, 30), (100, 200, 300)], rng)
    assert sized["envelope_low"] == 1000 and sized["envelope_high"] == 9000, sized
    assert sized["p10"] <= sized["p50"] <= sized["p90"], sized
    assert sized["envelope_low"] <= sized["p10"], sized  # P10 above the bound-wise floor
    assert sized["p90"] <= sized["envelope_high"], sized
    assert all(isinstance(sized[k], int) for k in
               ("p10", "p50", "p90", "envelope_low", "envelope_high")), sized
    # Hand-computed degenerate triangular: low=mid=high -> every draw equals the
    # point, all quantiles equal (the CSV lock still forbids such rows).
    d = sm.simulate_layer([(7, 7, 7)], random.Random(1))
    assert d == {"p10": 7, "p50": 7, "p90": 7, "envelope_low": 7, "envelope_high": 7}, d
    # Same seed, same bands -> identical draws (reproducibility anchor).
    a = sm.simulate_layer([(10, 20, 30), (100, 200, 300)], random.Random(42))
    b = sm.simulate_layer([(10, 20, 30), (100, 200, 300)], random.Random(42))
    assert a == b, (a, b)
    # Triangular mode sits at mid: E[X] == (lo+hi+mode)/3 sanity on the median.
    big = sm.simulate_layer([(100, 600, 1000)], random.Random(7), draws=100_000)
    assert 550 <= big["p50"] <= 650, big  # mean 566.7; P50 near it for symmetric-ish mode


def g4_market_sizing_rows(tmp: Path) -> None:
    a, cfg = fixture(tmp)
    model = sm.load_package_model(cfg)
    out = sm.build_market_sizing(sm.read_assumptions(a, [n for n, _dn, _d in model]),
                                 model, random.Random(sm.MC_SEED))
    assert list(out.columns) == sm.OUT_COLUMNS
    # package order = config order; layer order = LAYERS; absent layers emit no row
    assert list(out["package"]) == ["zeta_pkg", "zeta_pkg", "zeta_pkg", "alpha_pkg"], list(out["package"])
    assert list(out["layer"]) == ["context", "tam", "som", "som"], list(out["layer"])
    zeta_tam = out[(out["package"] == "zeta_pkg") & (out["layer"] == "tam")].iloc[0]
    assert int(zeta_tam["envelope_low"]) == 1000 and int(zeta_tam["envelope_high"]) == 9000
    assert zeta_tam["unit"] == "EUR/year"  # two-row product
    assert zeta_tam["test_in_validation"] == "y"  # labs row is y
    zeta_ctx = out[(out["package"] == "zeta_pkg") & (out["layer"] == "context")].iloc[0]
    assert zeta_ctx["unit"] == "EUR/year"  # single-row verbatim unit
    assert int(zeta_ctx["envelope_low"]) == 2000 and int(zeta_ctx["envelope_high"]) == 8000
    assert zeta_ctx["test_in_validation"] == "n"
    alpha_som = out[out["package"] == "alpha_pkg"].iloc[0]
    assert int(alpha_som["envelope_low"]) == 1000 and int(alpha_som["envelope_high"]) == 9000
    assert alpha_som["test_in_validation"] == "y"
    # config/data mismatch fails loudly (package absent from assumptions)
    _expect_valueerror("missing package rows",
                       lambda: sm.build_market_sizing(
                           sm.read_assumptions(a, ["zeta_pkg", "alpha_pkg", "ghost_pkg"]),
                           [("zeta_pkg", "Zeta Package", "deep"), ("alpha_pkg", "Alpha Package", "light"),
                            ("ghost_pkg", "Ghost", "light")],
                           random.Random(1)),
                       "ghost_pkg")


# --- integration gates ----------------------------------------------------------------------


def g5_determinism(tmp: Path) -> None:
    """Fixed seed -> two runs byte-identical (MC reproducibility gate, D-05)."""
    a, cfg = fixture(tmp)
    out_a, out_b = tmp / "a", tmp / "b"
    assert sm.main(assumptions_path=a, out_dir=out_a, config_path=cfg) == 0
    assert sm.main(assumptions_path=a, out_dir=out_b, config_path=cfg) == 0
    sha_a, sha_b = _sha_map(out_a), _sha_map(out_b)
    assert set(sha_a) == {"market_sizing.csv"} == set(sha_b)
    assert sha_a == sha_b, "two runs into separate dirs must be byte-identical"
    # idempotent overwrite in place: no error, no new files, same bytes
    before = _sha_map(out_a)
    assert sm.main(assumptions_path=a, out_dir=out_a, config_path=cfg) == 0
    assert _sha_map(out_a) == before


def g6_end_to_end(tmp: Path) -> None:
    a, cfg = fixture(tmp)
    out = tmp / "out"  # does not exist yet -> must be created, no crash
    assert sm.main(assumptions_path=a, out_dir=out, config_path=cfg) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == ["market_sizing.csv"], names  # exactly one, no manifests/dated dirs
    assert not list(out.glob("*.json")) and not list(out.glob("*/*"))
    text = (out / "market_sizing.csv").read_text("utf-8")
    lines = text.splitlines()
    assert lines[0] == ",".join(sm.OUT_COLUMNS)
    assert len(lines) == 5  # header + 3 zeta layers + 1 alpha som row
    # row order: zeta context/tam/som then alpha som (config x LAYERS order)
    assert [l.split(",")[0:2] for l in lines[1:]] == [
        ["zeta_pkg", "context"], ["zeta_pkg", "tam"], ["zeta_pkg", "som"], ["alpha_pkg", "som"]]
    low = text.lower()
    assert "nan" not in low and ".0," not in low and "e+" not in low  # ints plain, no floats
    assert text.endswith("\n") and "\r" not in text  # lineterminator="\n"


def g7_envelope_vs_quantiles(tmp: Path) -> None:
    """Envelope columns are the bound-wise products, never the headline (D-05)."""
    a, cfg = fixture(tmp)
    out = tmp / "env"
    assert sm.main(assumptions_path=a, out_dir=out, config_path=cfg) == 0
    df = pd.read_csv(out / "market_sizing.csv", dtype=str, keep_default_na=False)
    for rec in df.itertuples(index=False):
        p10, p50, p90 = int(rec.p10), int(rec.p50), int(rec.p90)
        el, eh = int(rec.envelope_low), int(rec.envelope_high)
        assert el <= p10 <= p50 <= p90 <= eh, rec
        assert p10 != el or el == eh  # MC P10 strictly inside the envelope floor
    # headline is the som layer (D-04): every package has a som row
    for pkg in FIXTURE_PACKAGES:
        assert "som" in set(df[df["package"] == pkg]["layer"]), pkg


def g8_platform_revenue(tmp: Path) -> None:
    """Platform step contract (issue-57 WI-16): band validation, rng order,
    draw-wise total identity, determinism, and loud failure modes."""
    def _expect_ve(label: str, fn, needle: str) -> None:
        try:
            fn()
        except ValueError as exc:
            assert needle in str(exc), f"{label}: expected {needle!r} in {exc!r}"
        else:
            raise AssertionError(f"{label}: expected ValueError naming {needle!r}")

    # (a) read_platform_band: happy path on the locked 8-col schema.
    good = _write(tmp, "platform.csv",
                  "field,low,mid,high,unit,source,confidence,test_in_validation\n"
                  "referral_rate_pct,3,4,5,percent of routed work,f,low,n\n")
    assert sm.read_platform_band(good) == (3, 4, 5)
    _expect_ve("header", lambda: sm.read_platform_band(_write(tmp, "ph.csv",
             "field,low,mid,high\nreferral_rate_pct,3,4,5\n")), "header")
    _expect_ve("two rows", lambda: sm.read_platform_band(_write(tmp, "p2.csv",
             "field,low,mid,high,unit,source,confidence,test_in_validation\n"
             "referral_rate_pct,3,4,5,u,f,low,n\n"
             "referral_rate_pct,3,4,5,u,f,low,n\n")), "exactly one")
    _expect_ve("field name", lambda: sm.read_platform_band(_write(tmp, "pf.csv",
             "field,low,mid,high,unit,source,confidence,test_in_validation\n"
             "commission_pct,3,4,5,u,f,low,n")), "referral_rate_pct")
    _expect_ve("flat band", lambda: sm.read_platform_band(_write(tmp, "pl.csv",
             "field,low,mid,high,unit,source,confidence,test_in_validation\n"
             "referral_rate_pct,4,4,5,u,f,low,n")), "low < mid < high")
    _expect_ve("non-integer", lambda: sm.read_platform_band(_write(tmp, "pn.csv",
             "field,low,mid,high,unit,source,confidence,test_in_validation\n"
             "referral_rate_pct,3,four,5,u,f,low,n")), "non-integer")
    _expect_ve("empty source", lambda: sm.read_platform_band(_write(tmp, "ps.csv",
             "field,low,mid,high,unit,source,confidence,test_in_validation\n"
             "referral_rate_pct,3,4,5,u,,low,n")), "source must not be empty")
    try:
        sm.read_platform_band(tmp / "absent.csv")
    except FileNotFoundError as exc:
        assert "platform.csv is required" in str(exc), exc
    else:
        raise AssertionError("missing platform.csv must fail loudly")

    # (b) rng-order guarantee: build_market_sizing with and without the
    # collect_som sink emits IDENTICAL rows (the sink never draws).
    a, cfg = fixture(tmp)
    model = sm.load_package_model(cfg)
    names = [n for n, _dn, _d in model]
    adf = sm.read_assumptions(a, names)
    plain = sm.build_market_sizing(adf, model, random.Random(sm.MC_SEED))
    sink: dict[str, list[float]] = {}
    collected = sm.build_market_sizing(adf, sm.load_package_model(cfg),
                                       random.Random(sm.MC_SEED), collect_som=sink)
    assert plain.equals(collected), "collect_som perturbed the market rows"
    assert list(sink) == names  # config insertion order, one column per package
    assert all(len(v) == sm.MC_DRAWS for v in sink.values())

    # (c) build_platform_revenue: deterministic under a fixed seed, monotonic,
    # and the total column is the DRAW-WISE sum (quantiles are not additive).
    draws = 200
    som = {"p1_venospatial": [float(i) + 1.0 for i in range(draws)],
           "p5_venomate": [10.0] * draws,
           "p6_gem": [5.0] * draws}
    got = sm.build_platform_revenue(som, (3, 4, 5), random.Random(7), draws=draws)
    assert list(got.columns) == sm.PLATFORM_OUT_COLUMNS
    assert list(got["component"]) == ["profiles", "referral", "total"]
    for rec in got.itertuples(index=False):
        assert int(rec.p10) <= int(rec.p50) <= int(rec.p90), rec
    # profiles row == the p5 column's quantiles (reused, never re-drawn).
    assert (int(got.iloc[0]["p10"]), int(got.iloc[0]["p50"]), int(got.iloc[0]["p90"])) \
        == (10, 10, 10), got.iloc[0].tolist()
    # element-wise reproduction of the referral/total columns from the seed:
    # routed[i] = p1[i] + p6[i] (insertion order, p5 excluded), rate drawn
    # once per iteration with rng.triangular(lo, hi, mid) (D-05 order).
    rng7 = random.Random(7)
    rate = [rng7.triangular(3, 5, 4) / 100.0 for _ in range(draws)]
    exp_referral = sorted((som["p1_venospatial"][i] + som["p6_gem"][i]) * q
                          for i, q in enumerate(rate))
    exp_total = sorted(10.0 + x for x in exp_referral)
    p50_ref = int(round(exp_referral[math.ceil(0.5 * draws) - 1]))
    p50_tot = int(round(exp_total[math.ceil(0.5 * draws) - 1]))
    assert int(got.iloc[1]["p50"]) == p50_ref, (got.iloc[1]["p50"], p50_ref)
    assert int(got.iloc[2]["p50"]) == p50_tot, (got.iloc[2]["p50"], p50_tot)
    # determinism: two runs on the same seed give identical frames.
    again = sm.build_platform_revenue(som, (3, 4, 5), random.Random(7), draws=draws)
    assert got.equals(again)
    # (d) loud failures: no profile package / no routed package.
    _expect_ve("no profile", lambda: sm.build_platform_revenue(
        {"p1_venospatial": [1.0] * draws}, (3, 4, 5), random.Random(1), draws=draws),
        "profile package")
    _expect_ve("no routed", lambda: sm.build_platform_revenue(
        {"p5_venomate": [1.0] * draws}, (3, 4, 5), random.Random(1), draws=draws),
        "at least one routed")
    # (e) fixture models (zeta/alpha) skip the step end to end: exactly one
    # output file, no platform artifact, no partial state.
    out = tmp / "skip"
    assert sm.main(assumptions_path=a, out_dir=out, config_path=cfg) == 0
    assert sorted(p.name for p in out.iterdir()) == ["market_sizing.csv"]


def g_int_real_data(tmp: Path) -> None:
    """Offline smoke over the REAL committed assumptions v2 + config; tree untouched."""
    processed = REPO_ROOT / "data" / "processed"
    before = _sha_map(processed) if processed.is_dir() else {}
    out = tmp / "real_out"
    assert sm.main(out_dir=out) == 0  # defaults: repo assumptions.csv + packages.yaml
    after = _sha_map(processed) if processed.is_dir() else {}
    assert before == after, "G-INT must not touch the real data/processed/ tree"

    text = (out / "market_sizing.csv").read_text("utf-8")
    assert text == BASELINE, f"baseline bytes drift:\n{text!r}"
    # Platform artifact (issue-57 WI-16): both files land, the platform bytes
    # match the committed baseline, and the market rows are byte-identical to
    # the BASELINE above — the assertion that the post-loop platform step
    # never perturbed the package rng sequence.
    names = sorted(p.name for p in out.iterdir())
    assert names == ["market_sizing.csv", "platform_revenue.csv"], names
    ptext = (out / "platform_revenue.csv").read_text("utf-8")
    assert ptext == PLATFORM_BASELINE, f"platform baseline bytes drift:\n{ptext!r}"
    # determinism: a second full run into a separate dir is byte-identical.
    out2 = tmp / "real_out2"
    assert sm.main(out_dir=out2) == 0
    assert (out2 / "platform_revenue.csv").read_bytes() \
        == (out / "platform_revenue.csv").read_bytes()
    assert (out2 / "market_sizing.csv").read_bytes() == (out / "market_sizing.csv").read_bytes()
    pdf = pd.read_csv(out / "platform_revenue.csv", dtype=str, keep_default_na=False)
    assert list(pdf.columns) == sm.PLATFORM_OUT_COLUMNS
    assert list(pdf["component"]) == ["profiles", "referral", "total"]
    for rec in pdf.itertuples(index=False):
        assert int(rec.p10) <= int(rec.p50) <= int(rec.p90), rec
    # the profiles row IS the p5 som row (reused column, never re-drawn).
    mdf = pd.read_csv(out / "market_sizing.csv", dtype=str, keep_default_na=False)
    p5som = mdf[(mdf["package"] == "p5_venomate") & (mdf["layer"] == "som")].iloc[0]
    prof = pdf.iloc[0]
    assert (prof["p10"], prof["p50"], prof["p90"]) == \
        (p5som["p10"], p5som["p50"], p5som["p90"]), (dict(prof), dict(p5som))
    # plain integer cells, utf-8 \n, no floats/sci notation (same lock as market)
    assert ptext.endswith("\n") and "\r" not in ptext
    for line in ptext.splitlines()[1:]:
        for cell in line.split(",")[1:]:
            assert cell.isdigit(), (line, cell)
    pkgs = _names(sm.PACKAGES_PATH)
    assert len(pkgs) == 6
    df = pd.read_csv(out / "market_sizing.csv", dtype=str, keep_default_na=False)
    assert list(dict.fromkeys(df["package"])) == pkgs  # config order end to end
    assert len(df) == 15  # 3 deep x 3 layers + 3 light x 2 layers (no context invented)
    # headline per package = SOM exists and is y-flagged for all six (D-04/D-06)
    som = df[df["layer"] == "som"]
    assert list(som["package"]) == pkgs and set(som["test_in_validation"]) == {"y"}
    # validation set: exactly 8 y rows in assumptions.csv v2 (D-14; 5-8 band rule
    # kept from 2026-09-25 — the count lives here because size_markets itself
    # stays shape-agnostic)
    adf = pd.read_csv(sm.ASSUMPTIONS_PATH, dtype=str, keep_default_na=False)
    assert list(adf.columns) == sm.ASSUMPTIONS_HEADER
    ys = adf[adf["test_in_validation"] == "y"]
    assert 5 <= len(ys) <= 8, len(ys)
    assert len(ys) == 8, len(ys)
    # the 8 = 6 som capacity rows + 2 tam population drivers (V1..V8)
    assert len(ys[ys["layer"] == "som"]) == 6 and len(ys[ys["layer"] == "tam"]) == 2, ys
    # som capacity rows carry confidence=low + written basis (D-06)
    caps = ys[ys["parameter"].isin(["seats_per_year", "engagements_per_year",
                                    "syntheses_per_year", "paying_profiles",
                                    "screens_per_year"])]
    assert len(caps) == 6 and set(caps["confidence"]) == {"low"}, caps
    assert all(len(str(s)) > 40 for s in caps["source"]), "capacity rows need a written basis"
    # D-19 source-text fix: banned provenance strings gone from the galleria rows
    for s in adf[adf["package"] == "p6_gem"]["source"]:
        assert "Notion" not in s and "published CRO quotes" not in s, s
    # monetary cells are plain integer strings (no .0 / scientific notation)
    for line in text.splitlines()[1:]:
        for cell in line.split(",")[2:7]:
            assert cell.isdigit() or (cell.startswith("-") and cell[1:].isdigit()), (line, cell)


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("G-0", g0_no_common_import),
        ("G-1", g1_package_model),
        ("G-2", g2_read_assumptions),
        ("G-3", g3_simulate_layer),
        ("G-4", g4_market_sizing_rows),
        ("G-5", g5_determinism),
        ("G-6", g6_end_to_end),
        ("G-7", g7_envelope_vs_quantiles),
        ("G-8", g8_platform_revenue),
        ("G-INT", g_int_real_data),
    ):
        with tempfile.TemporaryDirectory(prefix="check_markets_") as td:
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
