"""size_markets: assumptions.csv v2 -> data/processed/market_sizing.csv v2 (issue 53).

Three sizing layers per package (D-04): `context` (generic market, never a
headline), `tam` (rebuilt around the actual product), `som` (capacity × stated
price, analyst capacity). The headline per package is SOM. Band math (D-05):
each assumption row ~ independent triangular(low, mid, high), 10,000 draws,
fixed seed; the layer value is the PRODUCT of its rows' draws per iteration.
Reported quantiles: P10 / P50 / P90 (nearest-rank, ceil). The bound-wise
products (low×low / high×high) are kept as `envelope_low`/`envelope_high`
columns, never as the headline. This replaces the issue-10 bound-wise product
and the divergence cross-check column (decisions.md 2026-10-05 supersession
blocks; the 2026-10-05 stdlib-MC ruling: Python stdlib `random` only, no new
dependency — call signature `rng.triangular(lo, hi, mid)`, positional order
low, HIGH, then MODE per CPython docs, specs/issue-53-extdocs.md).

Locked v2 schemas:
  assumptions.csv: id,package,layer,parameter,low,mid,high,unit,source,
                   confidence,test_in_validation
                   (decisions.md 2026-10-05 superseding the 2026-09-25 lock)
  market_sizing.csv: package,layer,p10,p50,p90,envelope_low,envelope_high,
                     unit,test_in_validation
  platform.csv (manual, issue-57 WI-16): field,low,mid,high,unit,source,
                     confidence,test_in_validation — one referral_rate_pct row
  platform_revenue.csv (generated, issue-57 WI-16): component,p10,p50,p90

Platform revenue step (issue-57 WI-16; decisions.md 2026-10-06): executed
strictly AFTER the full package loop, so the rng consumption order of the 15
market_sizing rows is unchanged and byte-identical. One referral-rate band
(VenoMate's 3-5% on work routed to any package, D-18) is drawn 10,000 times
against the element-wise SUM of the stored som product columns of every
config package except the profile package `p5_venomate` — whose som IS the
profile-fee component, never routed work. Components: `profiles` (p5 som
column), `referral` (rate × routed sum), `total` (profiles + referral,
summed draw-wise BEFORE quantile extraction — quantiles are not additive).
The routed base assumes 100% of deliverable package work is routed: an
analyst ceiling stated in the platform.csv source cell (confidence=low).
Models without the profile package (gate fixtures) skip the step.

Shape rules re-expressed per layer (replacing the deep-3/light-1 inventory):
every config package must carry a `som` layer of exactly two rows (one count
parameter, one monetary rate — capacity × stated price); `tam`, when present,
exactly two rows (same one-count-one-rate product shape); `context`, when
present, exactly one band row. Strict low < mid < high is KEPT verbatim from
the 2026-09-25 lock (never point estimates); so are the EUR-unit rule and
config-driven package order.

Determinism (kept from the 2026-09-28/29 contracts, superseded only in column
content): a single random.Random(MC_SEED) instance consumed in fixed iteration
order (config package order × LAYERS order × file row order) makes two runs
byte-identical; integer cells are plain decimal strings, empties never appear
(absent layers emit no row — documented via evidence_map, not invented);
`index=False`, utf-8, lineterminator "\\n"; plain overwrite, no dated dirs, no
manifests — `_common.write_snapshot` is NOT reused here (AC11). Fully offline:
no network, no env reads, no timestamps.

Run: uv run python scripts/size_markets.py
Gates: uv run python scripts/check_markets.py
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSUMPTIONS_PATH = REPO_ROOT / "data" / "manual" / "assumptions.csv"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
PACKAGES_PATH = REPO_ROOT / "config" / "packages.yaml"
PLATFORM_PATH = REPO_ROOT / "data" / "manual" / "platform.csv"

# Locked v2 header (decisions.md 2026-10-05 assumptions.csv supersession).
ASSUMPTIONS_HEADER = ["id", "package", "layer", "parameter", "low", "mid", "high",
                      "unit", "source", "confidence", "test_in_validation"]

# Locked platform.csv header (issue-57 WI-16; same band columns as the
# assumptions v2 schema, minus id/package/layer/parameter — one field row).
PLATFORM_HEADER = ["field", "low", "mid", "high", "unit", "source",
                   "confidence", "test_in_validation"]
PLATFORM_FIELD = "referral_rate_pct"

# Locked platform_revenue.csv output schema (issue-57 WI-16).
PLATFORM_OUT_COLUMNS = ["component", "p10", "p50", "p90"]

# The profile-fee package: its som layer IS VenoMate profile revenue and is
# never part of the routed-work referral base (D-18; issue-57 WI-16 design).
PLATFORM_PROFILE_PACKAGE = "p5_venomate"

# Locked v2 output schema (D-04/D-05; replaces the issue-10 10-column schema).
OUT_COLUMNS = ["package", "layer", "p10", "p50", "p90",
               "envelope_low", "envelope_high", "unit", "test_in_validation"]

LAYERS = ("context", "tam", "som")

# The count parameters whose unit is a plain count; every other row's unit
# must contain "EUR" (2026-09-25 EUR rule kept verbatim, inventory re-expressed
# per layer). A two-row layer is exactly one count row × one monetary row.
COUNT_PARAMS = frozenset({
    "active_labs", "institutes", "learner_pool", "model_labs",
    "seats_per_year", "engagements_per_year", "syntheses_per_year",
    "paying_profiles", "screens_per_year",
})

# Monte-Carlo contract (D-05 + decisions.md 2026-10-05 stdlib-MC entry):
# fixed integer seed, 10,000 draws, stdlib random only.
MC_SEED = 531005
MC_DRAWS = 10_000

DEPTH_VALUES = {"deep", "light"}


def _resolve(path: Path | str | None, default: Path) -> Path:
    """Explicit path as given (relative -> cwd), else the repo-root default."""
    return default if path is None else Path(path)


def load_package_model(config_path: Path | str = PACKAGES_PATH) -> list[tuple[str, str, str]]:
    """Ordered (name, display_name, depth) triples under top-level `packages:` (FR2).

    Raises ValueError when the list is missing/empty, an entry lacks
    `name`/`depth`, or `depth` is not deep|light — the pipeline is
    config-driven, never hard-coded (AC9, packages.yaml lock as renamed by
    issue-53 D-01).
    """
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = data.get("packages") or []
    if not isinstance(entries, list):
        raise ValueError(f"`packages:` in {config_path} must be a list, got {type(entries).__name__}")
    model: list[tuple[str, str, str]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"package entry #{i} in {config_path} is not a map: {entry!r}")
        name = entry.get("name")
        depth = entry.get("depth")
        display_name = entry.get("display_name", str(name))
        if not name:
            raise ValueError(f"package entry #{i} in {config_path} is missing `name`: {entry!r}")
        if not depth:
            raise ValueError(f"package {str(name)!r} in {config_path} is missing `depth`")
        if depth not in DEPTH_VALUES:
            raise ValueError(
                f"package {str(name)!r} has depth {str(depth)!r}, "
                f"expected one of {sorted(DEPTH_VALUES)}"
            )
        model.append((str(name), str(display_name), str(depth)))
    if not model:
        raise ValueError(f"no package names found under `packages:` in {config_path}")
    return model


def read_assumptions(assumptions_path: Path | str, packages: list[str]) -> pd.DataFrame:
    """Read and loudly validate the v2 assumption inventory (D-04/D-05/D-14).

    dtype=str + keep_default_na=False (pandas 3.0 pattern from build_tables):
    every cell stays a verbatim string, empty cells stay "". Raises ValueError
    naming the offender on: header != the locked v2 11-column schema (exact
    order, not set match); package not in config; layer not in LAYERS;
    non-integer low/mid/high; NOT strict low < mid < high (never point
    estimates — 2026-09-25 rule kept); a monetary unit without "EUR"; a
    package without a `som` layer; a `som` or `tam` layer whose row count is
    not exactly two or whose two rows are not exactly one count parameter and
    one monetary parameter; a `context` layer with more than one row.
    """
    df = pd.read_csv(assumptions_path, dtype=str, keep_default_na=False)
    if list(df.columns) != ASSUMPTIONS_HEADER:
        raise ValueError(
            f"{assumptions_path}: header {list(df.columns)} does not match the locked "
            f"assumptions.csv v2 schema {ASSUMPTIONS_HEADER} (exact column order required)"
        )
    known = set(packages)
    layers_by_pkg: dict[str, dict[str, list[str]]] = {}
    for rec in df.itertuples(index=False):
        pkg = str(rec.package)
        layer = str(rec.layer)
        where = f"row id={rec.id!r} ({pkg}/{layer}/{rec.parameter})"
        if pkg not in known:
            raise ValueError(f"{where}: package {pkg!r} is not in config/packages.yaml {sorted(known)}")
        if layer not in LAYERS:
            raise ValueError(f"{where}: layer {layer!r} not in {list(LAYERS)}")
        bounds: dict[str, int] = {}
        for col in ("low", "mid", "high"):
            text = str(getattr(rec, col)).strip()
            try:
                bounds[col] = int(text)
            except ValueError:
                raise ValueError(f"{where}: non-integer {col} value {text!r}") from None
        if not bounds["low"] < bounds["mid"] < bounds["high"]:
            raise ValueError(
                f"{where}: bounds must be strictly low < mid < high, got "
                f"{rec.low} / {rec.mid} / {rec.high} (never point estimates, decisions.md 2026-09-25 kept)"
            )
        param = str(rec.parameter)
        if param not in COUNT_PARAMS and "EUR" not in str(rec.unit):
            raise ValueError(
                f"{where}: monetary parameter must carry a unit containing 'EUR', got {str(rec.unit)!r}"
            )
        layers_by_pkg.setdefault(pkg, {}).setdefault(layer, []).append(param)

    # Layer shape per package (D-04): som required, 1 count x 1 monetary;
    # tam optional product, same shape; context optional single band row.
    for pkg, by_layer in layers_by_pkg.items():
        for layer in sorted(by_layer):
            params = by_layer[layer]
            if layer == "context":
                if len(params) != 1:
                    raise ValueError(
                        f"package {pkg!r} context layer has {len(params)} rows {params}, "
                        f"expected exactly one generic-market band row"
                    )
                continue
            if len(params) != 2:
                raise ValueError(
                    f"package {pkg!r} {layer} layer has {len(params)} rows {params}, "
                    f"expected exactly two (capacity/count x rate/price)"
                )
            counts = sum(1 for p in params if p in COUNT_PARAMS)
            if counts != 1:
                raise ValueError(
                    f"package {pkg!r} {layer} layer rows {params} must be exactly one count "
                    f"parameter (from {sorted(COUNT_PARAMS)}) and one monetary parameter"
                )
        if "som" not in by_layer:
            raise ValueError(
                f"package {pkg!r} has no `som` layer — the headline per package is SOM (D-04)"
            )
    return df


def _quantiles(values: list[float], draws: int) -> dict:
    """Nearest-rank (ceil) P10/P50/P90 over a sorted copy of the draws."""
    s = sorted(values)
    return {
        "p10": int(round(s[math.ceil(0.10 * draws) - 1])),
        "p50": int(round(s[math.ceil(0.50 * draws) - 1])),
        "p90": int(round(s[math.ceil(0.90 * draws) - 1])),
    }


def simulate_layer_products(bands: list[tuple[int, int, int]], rng: random.Random,
                            draws: int = MC_DRAWS) -> tuple[dict, list[float]]:
    """MC core of `simulate_layer`, plus the UNSORTED per-iteration products.

    Returns (stats, products) where stats carries the quantile/envelope cells
    and `products` is the draw-ordered product column — the platform revenue
    step reuses these columns element-wise (sums must be taken over draw
    order, never over a sorted column). The rng consumption sequence is
    exactly one full triangular column per band row, in band order.
    """
    products = [1.0] * draws
    for (lo, mid, hi) in bands:
        col = [rng.triangular(lo, hi, mid) for _ in range(draws)]
        products = [p * c for p, c in zip(products, col)]
    stats = _quantiles(products, draws)
    stats["envelope_low"] = int(round(math.prod(lo for lo, _mid, _hi in bands)))
    stats["envelope_high"] = int(round(math.prod(hi for _lo, _mid, hi in bands)))
    return stats, products


def simulate_layer(bands: list[tuple[int, int, int]], rng: random.Random,
                   draws: int = MC_DRAWS) -> dict:
    """Independent triangular Monte Carlo for one package × layer (D-05).

    `bands` are (low, mid, high) integer triples in file order. Each iteration
    multiplies one `rng.triangular(low, high, mode)` draw per row — positional
    order low, HIGH, then MODE per CPython docs (specs/issue-53-extdocs.md;
    the spec's `triangular(low, mode, high)` spelling is a bug: mode must lie
    within [low, high]). Quantiles are nearest-rank (ceil) over the sorted
    products; envelope_low/high are the bound-wise products (never headline).
    A degenerate single band low=mid=high yields all-equal quantiles (the
    CSV-level strict-inequality lock still forbids such rows in assumptions).
    """
    stats, _products = simulate_layer_products(bands, rng, draws)
    return stats


def build_market_sizing(df: pd.DataFrame, model: list[tuple[str, str, str]],
                        rng: random.Random,
                        collect_som: dict[str, list[float]] | None = None) -> pd.DataFrame:
    """One row per package × present layer, columns == OUT_COLUMNS (D-04/D-05).

    Rows are emitted in config package order × LAYERS order; absent layers
    emit NO row (documented via config/evidence_map.yaml, never invented —
    the 2026-10-04 "pad the snapshot-derived tables" rejection extended).
    The layer value is the fixed-seed MC over the layer's rows; unit is
    "EUR/year" for a two-row product layer and the single row's unit
    verbatim for a context band. test_in_validation is "y" when any row of
    the layer is "y". Validation completes before the first draw.

    When `collect_som` is passed, each package's UNSORTED som product column
    is stored there under the package name (config insertion order) for the
    platform revenue step (issue-57 WI-16); the rng sequence is unchanged.
    """
    rows: list[dict] = []
    for pkg, _display_name, _depth in model:
        sub = df[df["package"] == pkg]
        if sub.empty:
            raise ValueError(
                f"config declares package {pkg!r} but assumptions.csv has no rows "
                f"for it — every config package must be sized"
            )
        for layer in LAYERS:
            lsub = sub[sub["layer"] == layer]
            if lsub.empty:
                continue
            bands = [(int(r.low), int(r.mid), int(r.high))
                     for r in lsub.itertuples(index=False)]
            sized, products = simulate_layer_products(bands, rng)
            if collect_som is not None and layer == "som":
                collect_som[pkg] = products
            units = sorted({str(r.unit) for r in lsub.itertuples(index=False)})
            unit = units[0] if len(bands) == 1 and len(units) == 1 else "EUR/year"
            flags = {str(r.test_in_validation) for r in lsub.itertuples(index=False)}
            rows.append({
                "package": pkg,
                "layer": layer,
                **sized,
                "unit": unit,
                "test_in_validation": "y" if "y" in flags else "n",
            })
    out = pd.DataFrame(rows, columns=OUT_COLUMNS)
    return out


def write_table(df: pd.DataFrame, out_dir: Path, stem: str = "market_sizing") -> Path:
    """Write the sized-market table under the byte-determinism contract (AC6/FR7).

    Plain overwrite (idempotent), no dated dirs, no manifests — this is why
    _common.write_snapshot is NOT reused here (AC11).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.csv"
    df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


def read_platform_band(platform_path: Path | str = PLATFORM_PATH) -> tuple[int, int, int]:
    """Read and loudly validate the one-row platform.csv referral band.

    Raises ValueError/KeyError naming the offender on: header != the locked
    8-column platform schema (exact order); not exactly one data row; field
    != referral_rate_pct; non-integer low/mid/high; NOT strict
    low < mid < high (the never-point-estimates rule, kept from the
    assumptions lock); empty unit or source. Validation completes before
    any draw (validate-all-then-write-all).
    """
    platform_path = Path(platform_path)
    if not platform_path.is_file():
        raise FileNotFoundError(
            f"{platform_path}: platform.csv is required for the platform "
            f"revenue step (issue-57 WI-16) — never invent the referral band"
        )
    df = pd.read_csv(platform_path, dtype=str, keep_default_na=False)
    if list(df.columns) != PLATFORM_HEADER:
        raise ValueError(
            f"{platform_path}: header {list(df.columns)} does not match the locked "
            f"platform.csv schema {PLATFORM_HEADER} (exact column order required)"
        )
    if len(df) != 1:
        raise ValueError(
            f"{platform_path}: expected exactly one referral_rate_pct row, got {len(df)}"
        )
    rec = df.iloc[0]
    if str(rec.field) != PLATFORM_FIELD:
        raise ValueError(
            f"{platform_path}: field {str(rec.field)!r} is not {PLATFORM_FIELD!r}"
        )
    bounds: dict[str, int] = {}
    for col in ("low", "mid", "high"):
        text = str(getattr(rec, col)).strip()
        try:
            bounds[col] = int(text)
        except ValueError:
            raise ValueError(
                f"{platform_path}: non-integer {col} value {text!r}"
            ) from None
    if not bounds["low"] < bounds["mid"] < bounds["high"]:
        raise ValueError(
            f"{platform_path}: referral band must be strictly low < mid < high, got "
            f"{rec.low} / {rec.mid} / {rec.high}"
        )
    for col in ("unit", "source"):
        if not str(getattr(rec, col)).strip():
            raise ValueError(f"{platform_path}: {col} must not be empty")
    return bounds["low"], bounds["mid"], bounds["high"]


def build_platform_revenue(som_products: dict[str, list[float]],
                           band: tuple[int, int, int], rng: random.Random,
                           draws: int = MC_DRAWS) -> pd.DataFrame:
    """Platform-level revenue bands: profiles, referral, total (issue-57 WI-16).

    Called strictly AFTER the full package loop so the package rng sequence
    is untouched. The referral rate is drawn once per iteration
    (`rng.triangular(lo, hi, mid)`, D-05 positional order) and applied to the
    element-wise sum of every package's stored som product column except
    PLATFORM_PROFILE_PACKAGE — whose som IS the profile-fee component. The
    routed base assumes 100% of deliverable package work is routed: an
    analyst ceiling stated in platform.csv (confidence=low). `total` sums
    the two component draws BEFORE quantile extraction (quantiles are not
    additive). Rows in profiles/referral/total order.
    """
    if PLATFORM_PROFILE_PACKAGE not in som_products:
        raise ValueError(
            f"profile package {PLATFORM_PROFILE_PACKAGE!r} has no som product column — "
            f"the platform step must run over the full package loop's columns"
        )
    if len(som_products) < 2:
        raise ValueError("referral base needs at least one routed package besides "
                         f"{PLATFORM_PROFILE_PACKAGE!r}, got {sorted(som_products)}")
    routed_pkgs = [p for p in som_products if p != PLATFORM_PROFILE_PACKAGE]
    routed = [sum(som_products[p][i] for p in routed_pkgs) for i in range(draws)]
    lo, mid, hi = band
    rate = [rng.triangular(lo, hi, mid) / 100.0 for _ in range(draws)]
    referral = [r * x for r, x in zip(routed, rate)]
    profiles = som_products[PLATFORM_PROFILE_PACKAGE]
    total = [a + b for a, b in zip(profiles, referral)]
    rows = [{"component": name, **_quantiles(col, draws)}
            for name, col in (("profiles", profiles), ("referral", referral),
                              ("total", total))]
    return pd.DataFrame(rows, columns=PLATFORM_OUT_COLUMNS)


def write_platform_table(df: pd.DataFrame, out_dir: Path) -> Path:
    """Write platform_revenue.csv under the same byte-determinism contract."""
    return write_table(df, out_dir, stem="platform_revenue")


def main(*, assumptions_path: Path | str | None = None,
         out_dir: Path | str | None = None,
         config_path: Path | str | None = None,
         platform_path: Path | str | None = None) -> int:
    """Size every package × layer from assumptions.csv with one fixed-seed MC (AC1).

    Returns 0. Keyword-only seams mirror build_tables.main so check_markets.py
    runs this in-process over fixtures in a tmp dir; defaults are
    REPO_ROOT-anchored, so the script works from any cwd. All validation
    completes before the first draw — a bad row means zero output
    (validate-all-then-write-all, build_tables AC9 precedent). The rng is
    created here (seed MC_SEED) and consumed in build_market_sizing's fixed
    iteration order: two runs are byte-identical (reproducibility gate).
    The platform revenue step (issue-57 WI-16) consumes the SAME rng strictly
    after the package loop — the market_sizing bytes are unaffected — and
    writes platform_revenue.csv next to market_sizing.csv. Fixture models
    without the profile package skip the step (no platform artifact, no
    partial state).
    """
    model = load_package_model(_resolve(config_path, PACKAGES_PATH))
    packages = [name for name, _display_name, _depth in model]
    df = read_assumptions(_resolve(assumptions_path, ASSUMPTIONS_PATH), packages)
    band: tuple[int, int, int] | None = None
    if PLATFORM_PROFILE_PACKAGE in packages:
        band = read_platform_band(_resolve(platform_path, PLATFORM_PATH))
    resolved_out = _resolve(out_dir, PROCESSED_DIR)
    rng = random.Random(MC_SEED)
    som_products: dict[str, list[float]] = {}
    sized = build_market_sizing(df, model, rng, collect_som=som_products)
    revenue: pd.DataFrame | None = None
    if band is not None:
        revenue = build_platform_revenue(som_products, band, rng)
    path = write_table(sized, resolved_out)
    print(f"wrote {path} ({len(sized)} rows)")
    if revenue is not None:
        ppath = write_platform_table(revenue, resolved_out)
        print(f"wrote {ppath} ({len(revenue)} rows)")
    else:
        print(f"platform step skipped: model has no {PLATFORM_PROFILE_PACKAGE!r} "
              "profile package (fixture model)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
