"""make_figures: data/processed/*.csv -> report/figures/*.png + report/generated/*.md
(issue 11; v2 schemas + roles per issue-53 D-03/D-05).

Reads the seven locked processed CSVs (decisions.md 2026-10-05 supersession
blocks over the 2026-09-28/29 contracts), validates them against
config/packages.yaml (six renamed keys) and config/evidence_map.yaml (n/a
cells are legitimately absent from snapshot-derived tables — documented, not
pulled, never padded), and writes exactly:

  report/figures/   fig_market_sizing.png        <- market_sizing.csv (Fig. M3:
                                                      P10/P50/P90 bands, SOM headline)
                    fig_ctgov_trends.png         <- ctgov_counts.csv
                    fig_cordis_funding.png       <- cordis_funding.csv + cordis_status.csv
                    fig_openalex_landscape.png   <- openalex_landscape.csv
                    fig_openalex_trend_multiples.png <- openalex_landscape.csv
                                                      (trend small-multiples)
                    fig_coverage.png             <- coverage.csv + overview.csv
                                                      (role matrix, v2)
  report/generated/ market_sizing.md  ctgov_counts.md  cordis_funding.md
                    openalex_landscape.md  coverage.md  overview.md

The full/thin/empty completeness labels are retired (D-03): their strings
must not appear in any output; coverage carries the declared role instead.

These names and data bindings are the locked artifact inventory for the
report sections (decisions.md 2026-09-29 figures contract as superseded
2026-10-05); chart aesthetics are sensible defaults. The script writes ONLY
under report/figures/ and report/generated/ — hand-written report/*.md prose
is never touched.

Determinism: tables are byte-deterministic (utf-8, lineterminator "\\n",
source-CSV column order, rows in source order — config-package order per
the 2026-09-29 ruling, never re-sorted — verbatim cells with empty cells
staying empty, a literal "|" in a cell escaped as "\\|", table-only files
with a single trailing newline, plain overwrite). PNGs are rendered with
the matplotlib Agg backend at pinned portrait figsize=(6.4, 7.6) and
dpi=150 in the default style with no interactive elements: determinism is
PIXEL-level (two runs decoded via matplotlib.image.imread and compared
with numpy.array_equal). Cross-machine PNG byte-reproducibility is
explicitly out — matplotlib embeds version/font-cache metadata in the
PNG bytes, so identical pixels on the same machine are the stable
contract, not identical bytes.

_common.write_snapshot is NOT reused here (dated dirs, FileExistsError
semantics and manifests fit raw pulls, not stable committed artifacts);
this script owns a local deterministic write_table(). The script is
self-contained: _common, build_tables and size_markets are NOT imported —
load_package_model re-implements the size_markets.py pattern by design
(decisions.md 2026-09-29 self-contained ruling). Fully offline: no HTTP
client is ever constructed, no timestamps.

Validation is loud and completes BEFORE any write (validate-all-then-
write-all, size_markets precedent): header drift, unknown or missing
packages, package blocks out of config order, non-integer counts,
non-numeric EUR cells, unknown evidence roles and duplicate rows all
raise ValueError naming the offender.

Run: uv run python scripts/make_figures.py   (or: make figures)
Gates: uv run python scripts/check_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive raster backend — must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
FIG_DIR = REPO_ROOT / "report" / "figures"
GEN_DIR = REPO_ROOT / "report" / "generated"
PACKAGES_PATH = REPO_ROOT / "config" / "packages.yaml"
EVIDENCE_MAP_PATH = REPO_ROOT / "config" / "evidence_map.yaml"

# Pinned rendering contract (light spec Constraints): fixed size + dpi,
# default style, no rcParams drift, no interactive elements. Portrait
# canvas per the issue-48 figure pass (F5): multi-panel figures stack.
FIGSIZE = (6.4, 7.6)
DPI = 150

DEPTH_VALUES = {"deep", "light"}

# Locked schemas v2 (decisions.md 2026-10-05 supersession blocks; issue-53
# D-03/D-05): exact column order, never inferred from the file.
LOCKED_SCHEMAS: dict[str, list[str]] = {
    "market_sizing": ["package", "layer", "p10", "p50", "p90",
                      "envelope_low", "envelope_high", "unit", "test_in_validation"],
    "ctgov_counts": ["package", "dimension", "key", "count"],
    "cordis_funding": ["package", "programme", "projects", "eu_contribution_eur"],
    "cordis_status": ["package", "status", "projects"],
    "openalex_landscape": ["package", "dimension", "key", "key_display_name", "count"],
    "coverage": ["package", "source", "snapshot_date", "record_count", "role"],
    "overview": ["package", "openalex_works", "ctgov_studies", "cordis_projects",
                 "cordis_eu_contribution_eur"],
}

# Locked artifact inventory (decisions.md 2026-09-29 as superseded 2026-10-05):
# six stem-matched .md tables — no cordis_status.md, it feeds only
# fig_cordis_funding.png — and the base PNGs (grid figures emit per-dimension
# files, counted dynamically). fig_market_sizing.png is the Fig. M3 candidate
# (issue-53 WI-07): P10/P50/P90 bands per package × layer with SOM headline;
# fig_openalex_trend_multiples.png is the trend small-multiples (second
# cut-candidate, delivered).
TABLE_STEMS = ("market_sizing", "ctgov_counts", "cordis_funding",
               "openalex_landscape", "coverage", "overview")
FIGURES = ("fig_market_sizing.png", "fig_cordis_funding.png", "fig_coverage.png",
           "fig_openalex_trend_multiples.png")

# Evidence roles locked by issue-53 D-03 (the full/thin/empty completeness
# labels are RETIRED — their strings must not appear in any output).
ROLES = ("driver", "context", "n/a")
OVERVIEW_COUNT_METRICS = ("openalex_works", "ctgov_studies", "cordis_projects")

# Snapshot-derived stems -> source key in the evidence_map (a package whose
# (package, source) role is n/a legitimately has no rows in that table —
# documented, not pulled; rows are still never padded).
STEM_SOURCE = {"openalex_landscape": "openalex", "ctgov_counts": "ctgov",
               "cordis_funding": "cordis", "cordis_status": "cordis"}

# Fixed palettes — aesthetics are defaults; colors are keyed by position,
# never by package name, so any config stays renderable.
PACKAGE_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                  "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
ROLE_COLORS = {"driver": "#4daf4a", "context": "#fdae61", "n/a": "#d6604d"}
ROLE_TEXT = {"driver": "black", "context": "black", "n/a": "white"}


def _resolve(path: Path | str | None, default: Path) -> Path:
    """Explicit path as given (relative -> cwd), else the repo-root default."""
    return default if path is None else Path(path)


def load_package_model(config_path: Path | str = PACKAGES_PATH) -> list[tuple[str, str, str]]:
    """Ordered (name, display_name, depth) triples under top-level `packages:` (FR2).

    Self-contained duplicate of the size_markets.py pattern (locked by the
    decisions.md 2026-09-29 self-contained ruling). Raises ValueError when
    the list is missing/empty, an entry lacks `name`/`depth`, or `depth` is
    not deep|light — the pipeline is config-driven, never hard-coded.
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


def read_processed(path: Path | str, header: list[str], packages: list[str],
                   optional: set[str] | None = None) -> pd.DataFrame:
    """Read and loudly validate one locked processed CSV (FR2, AC: never hard-coded).

    dtype=str + keep_default_na=False (pandas 3.0 pattern from
    size_markets/build_tables): every cell stays a verbatim string and
    empty cells stay "". Raises ValueError naming the offender on: header
    != the locked schema (exact order, not set match — catches renamed and
    reordered columns); a package not in config; package blocks out of
    config order, non-contiguous, or missing (rows are sorted by package
    then group key per the 2026-09-28/29 contracts, so each package must
    appear as exactly one contiguous block in config order). `optional`
    names packages that may legitimately be ABSENT (issue-53 D-03: a
    package × source whose declared role is n/a is documented, not pulled
    — never a padded zero row); present-but-unlisted packages still fail.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != list(header):
        raise ValueError(
            f"{path}: header {list(df.columns)} does not match the locked "
            f"schema {list(header)} (exact column order required)"
        )
    known = list(packages)
    allowed_missing = optional or set()
    seen: list[str] = []
    for i, rec in enumerate(df.itertuples(index=False, name=None)):
        pkg = str(rec[0])
        if pkg not in known:
            raise ValueError(f"{path} row {i + 2}: package {pkg!r} is not in config/packages.yaml {known}")
        if not seen or seen[-1] != pkg:
            seen.append(pkg)
    expected = [p for p in known if p not in allowed_missing]
    if seen != expected:
        raise ValueError(
            f"{path}: package blocks in row order {seen} do not match the config order "
            f"{expected} (missing, duplicated or out-of-order package blocks — rows are "
            f"sorted by package then group key, never re-sorted here; absent packages "
            f"must be declared n/a in config/evidence_map.yaml, never padded)"
        )
    return df


def _md_cell(text: str) -> str:
    """Escape a literal pipe for a GFM table cell; everything else verbatim."""
    return text.replace("|", "\\|")


def write_table(df: pd.DataFrame, out_dir: Path, stem: str) -> Path:
    """Render df as a stem-matched GFM pipe table under report/generated/ (FR3).

    Determinism contract (decisions.md 2026-09-28/29): column order ==
    source CSV header order; row order == df order (already config-package
    order — never re-sorted); cells rendered verbatim from the dtype=str
    read, so empty cells stay empty (the light-row 10-field erratum case)
    and integers stay plain decimal strings; a literal "|" is escaped as
    "\\|"; utf-8, "\\n" line endings, single trailing newline, table-only
    (no prose). Plain overwrite — no dated dirs, no manifests — which is
    why _common.write_snapshot is NOT reused here (AC: local helper).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.md"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(_md_cell(str(c)) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for rec in df.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(_md_cell(str(v)) for v in rec) + " |")
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


# --- figure data bindings (private derive helpers; the plot functions and
# --- check_figures.py G-4 both consume them, so the binding is by construction)


def _as_int(text: str, where: str, col: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{where}: non-integer {col} value {text!r}") from None


def _as_float(text: str, where: str, col: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{where}: non-numeric {col} value {text!r}") from None


def _market_sizing_bands(df: pd.DataFrame) -> list[tuple[str, str, int, int, int, int, int]]:
    """(package, layer, p10, p50, p90, envelope_low, envelope_high) in row order.

    fig_market_sizing (Fig. M3) binding, v2 (issue-53 D-04/D-05): the headline
    band is the simulation quantiles P10/P50/P90; the bound-wise envelope is
    carried for visibility, never as the headline. Inverted-band error path
    kept: quantiles must satisfy p10 <= p50 <= p90 and stay inside the
    envelope_low/high bounds; all cells must be non-negative integers.
    """
    out: list[tuple[str, str, int, int, int, int, int]] = []
    for i, rec in enumerate(df.itertuples(index=False, name=None)):
        where = f"market_sizing.csv row {i + 2}"
        vals = tuple(_as_int(str(v), where, c) for c, v in
                     zip(("p10", "p50", "p90", "envelope_low", "envelope_high"), rec[2:7]))
        if any(v < 0 for v in vals):
            raise ValueError(f"{where}: quantile/envelope cells must be >= 0, got {vals}")
        p10, p50, p90, el, eh = vals
        if not p10 <= p50 <= p90:
            raise ValueError(f"{where}: band must satisfy p10 <= p50 <= p90, got {vals[:3]}")
        if not (el <= p10 and p90 <= eh):
            raise ValueError(f"{where}: quantiles must sit inside the envelope "
                             f"low/high bounds, got {vals}")
        out.append((str(rec[0]), str(rec[1]), *vals))
    return out


def _ctgov_groups(df: pd.DataFrame, top_n: int = 10):
    """dimension -> (selected keys, {package: {key: count}}) + package order.

    fig_ctgov_trends binding. Keys are ranked by MAX count across packages
    (never summed across packages), ties keep first-appearance order via a
    stable sort; at most top_n keys per dimension are selected. Raises
    ValueError on duplicate (package, dimension, key) rows or non-integer
    counts.
    """
    dims: dict[str, dict[str, dict[str, int]]] = {}
    dim_order: list[str] = []
    pkg_order: list[str] = []
    for i, rec in enumerate(df.itertuples(index=False, name=None)):
        pkg, dim, key, count = (str(v) for v in rec)
        where = f"ctgov_counts.csv row {i + 2}"
        n = _as_int(count, where, "count")
        per = dims.setdefault(dim, {})
        if dim not in dim_order:
            dim_order.append(dim)
        cell = per.setdefault(pkg, {})
        if key in cell:
            raise ValueError(f"{where}: duplicate package/dimension/key {pkg}/{dim}/{key!r}")
        cell[key] = n
        if pkg not in pkg_order:
            pkg_order.append(pkg)
    out: dict[str, tuple[list[str], dict[str, dict[str, int]]]] = {}
    for dim in dim_order:
        first_seen: list[str] = []
        for pkg in pkg_order:
            for key in dims[dim].get(pkg, {}):
                if key not in first_seen:
                    first_seen.append(key)
        ranked = sorted(
            first_seen,
            key=lambda k: -max((dims[dim].get(p, {}).get(k, 0) for p in pkg_order), default=0),
        )
        out[dim] = (ranked[:top_n], dims[dim])
    return out, pkg_order


def _cordis_series(funding: pd.DataFrame, status: pd.DataFrame):
    """(funding, programmes, status, statuses, packages) — fig_cordis_funding binding.

    funding: package -> programme -> (projects:int, eur:str verbatim).
    status:  package -> status -> projects:int. The EUR cell is validated
    numeric but kept verbatim for labels; float() is used only for bar
    heights. Raises ValueError on duplicate rows or non-numeric cells.
    """
    fund: dict[str, dict[str, tuple[int, str]]] = {}
    programmes: list[str] = []
    pkgs: list[str] = []
    for i, rec in enumerate(funding.itertuples(index=False, name=None)):
        pkg, prog, projects, eur = (str(v) for v in rec)
        where = f"cordis_funding.csv row {i + 2}"
        n = _as_int(projects, where, "projects")
        _as_float(eur, where, "eu_contribution_eur")
        per = fund.setdefault(pkg, {})
        if prog in per:
            raise ValueError(f"{where}: duplicate package/programme {pkg}/{prog!r}")
        per[prog] = (n, eur)
        if prog not in programmes:
            programmes.append(prog)
        if pkg not in pkgs:
            pkgs.append(pkg)
    stat: dict[str, dict[str, int]] = {}
    statuses: list[str] = []
    for i, rec in enumerate(status.itertuples(index=False, name=None)):
        pkg, st, projects = (str(v) for v in rec)
        where = f"cordis_status.csv row {i + 2}"
        n = _as_int(projects, where, "projects")
        per = stat.setdefault(pkg, {})
        if st in per:
            raise ValueError(f"{where}: duplicate package/status {pkg}/{st!r}")
        per[st] = n
        if st not in statuses:
            statuses.append(st)
        if pkg not in pkgs:
            pkgs.append(pkg)
    return fund, programmes, stat, statuses, pkgs


def _openalex_topn(df: pd.DataFrame, top_n: int = 8):
    """dimension -> (selected keys, {package: {key: (display_name, count)}}) + packages.

    fig_openalex_landscape binding. The binding keys on `key` (unique per
    package+dimension) because key_display_name is a label only —
    institution names can repeat across distinct ids. Keys are ranked by
    MAX count across packages; counts are NEVER summed across packages or
    across marginals (decisions.md 2026-09-28 headline rule: only the
    publication_year marginal totals works, and this figure shows no
    totals). Ties keep first-appearance order (stable sort).
    """
    dims: dict[str, dict[str, dict[str, tuple[str, int]]]] = {}
    dim_order: list[str] = []
    displays: dict[str, list[str]] = {}
    pkg_order: list[str] = []
    for i, rec in enumerate(df.itertuples(index=False, name=None)):
        pkg, dim, key, display, count = (str(v) for v in rec)
        where = f"openalex_landscape.csv row {i + 2}"
        n = _as_int(count, where, "count")
        per = dims.setdefault(dim, {})
        if dim not in dim_order:
            dim_order.append(dim)
            displays[dim] = []
        cell = per.setdefault(pkg, {})
        if key in cell:
            raise ValueError(f"{where}: duplicate package/dimension/key {pkg}/{dim}/{key!r}")
        cell[key] = (display, n)
        if key not in displays[dim]:
            displays[dim].append(key)
        if pkg not in pkg_order:
            pkg_order.append(pkg)
    out: dict[str, tuple[list[str], dict[str, dict[str, tuple[str, int]]]]] = {}
    for dim in dim_order:
        ranked = sorted(
            displays[dim],
            key=lambda k: -max((dims[dim].get(p, {}).get(k, ("", 0))[1] for p in pkg_order), default=0),
        )
        out[dim] = (ranked[:top_n], dims[dim])
    return out, pkg_order


def _coverage_matrix(coverage: pd.DataFrame):
    """(matrix, packages, sources) — fig_coverage binding (v2 roles).

    matrix: package -> source -> (record_count:int, role). Roles are
    validated against the locked {driver, context, n/a} set (issue-53 D-03;
    the retired full/thin/empty completeness labels must not appear); rows
    are never dropped.
    """
    matrix: dict[str, dict[str, tuple[int, str]]] = {}
    pkg_order: list[str] = []
    src_order: list[str] = []
    for i, rec in enumerate(coverage.itertuples(index=False, name=None)):
        pkg, src, _snap, count, role = (str(v) for v in rec)
        where = f"coverage.csv row {i + 2}"
        n = _as_int(count, where, "record_count")
        if role not in ROLES:
            raise ValueError(f"{where}: role {role!r} not in {list(ROLES)}")
        per = matrix.setdefault(pkg, {})
        if src in per:
            raise ValueError(f"{where}: duplicate package/source {pkg}/{src!r}")
        per[src] = (n, role)
        if src not in src_order:
            src_order.append(src)
        if pkg not in pkg_order:
            pkg_order.append(pkg)
    return matrix, pkg_order, src_order


def _overview_counts(overview: pd.DataFrame) -> dict[str, dict[str, int]]:
    """package -> {metric: int} — fig_coverage overview panel binding.

    The three count columns are validated integer; cordis_eu_contribution_eur
    is validated numeric but not plotted on the count axis (scale).
    """
    out: dict[str, dict[str, int]] = {}
    for i, rec in enumerate(overview.itertuples(index=False, name=None)):
        pkg = str(rec[0])
        where = f"overview.csv row {i + 2}"
        if pkg in out:
            raise ValueError(f"{where}: duplicate package {pkg!r}")
        row = {col: _as_int(str(v), where, col)
               for col, v in zip(OVERVIEW_COUNT_METRICS, rec[1:4])}
        _as_float(str(rec[4]), where, "cordis_eu_contribution_eur")
        out[pkg] = row
    return out


def _save(fig: "plt.Figure", out_dir: Path, name: str) -> Path:
    """Plain-overwrite savefig at the pinned dpi + close (no pyplot state bleed)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.savefig(path)  # format inferred from .png; dpi from the figure; no bbox_inches='tight'
    plt.close(fig)
    return path


def plot_market_sizing(df: pd.DataFrame, out_dir: Path) -> Path:
    """fig_market_sizing.png (Fig. M3): P10–P90 band per package × layer.

    v2 (issue-53 D-04/D-05/WI-07): one horizontal band per package × layer
    row — the thick line spans P10..P90 of the fixed-seed simulation with
    P50 marked; the thin whisker spans the bound-wise envelope (kept for
    visibility, never the headline). SOM rows — the headline per package —
    are drawn bold and listed last within each package block; the plain-
    English title and EUR/year axis label state what the band is.
    """
    bands = _market_sizing_bands(df)
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    pkg_idx = {p: i for i, p in enumerate(dict.fromkeys(b[0] for b in bands))}
    for i, (pkg, layer, p10, p50, p90, el, eh) in enumerate(bands):
        color = PACKAGE_COLORS[pkg_idx[pkg] % len(PACKAGE_COLORS)]
        som = layer == "som"
        ax.hlines(i, el, eh, color=color, linewidth=0.9, alpha=0.45)  # envelope whisker
        ax.hlines(i, p10, p90, color=color, linewidth=4.0 if som else 2.2)
        ax.plot(p50, i, marker="o", color=color, markersize=7 if som else 5)
    ax.set_yticks(range(len(bands)),
                  [f"{pkg} · {layer}{'  (headline)' if layer == 'som' else ''}"
                   for pkg, layer, *_rest in bands], fontsize=8)
    ax.invert_yaxis()  # config order top-to-bottom, som last within package block
    if bands and all(min(b[2:7]) > 0 for b in bands):
        ax.set_xscale("log")  # bands span orders of magnitude
    ax.set_xlabel("EUR per year (log scale; thick band = P10–P90 of the simulation, "
                  "dot = P50, thin line = bound-wise envelope)")
    ax.set_title("Market sizing per package: simulated P10–P90 by layer, SOM is the headline")
    fig.tight_layout()
    return _save(fig, out_dir, "fig_market_sizing.png")


def plot_ctgov_dimensions(df: pd.DataFrame, out_dir: Path) -> list[Path]:
    """fig_ctgov_trends_*.png: one full-size bar chart per CT.gov dimension.

    Each dimension gets its own standalone portrait figure (no grid grouping)
    so labels, bars, and legends are fully readable. Files are numbered
    sequentially in dimension first-appearance order.
    """
    groups, pkgs = _ctgov_groups(df)
    paths: list[Path] = []
    for idx, dim in enumerate(groups, start=1):
        fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
        keys, per_pkg = groups[dim]
        n_bars = len(keys)
        n_pkgs = len(pkgs)
        width = 0.86 / max(n_pkgs, 1)
        for i, pkg in enumerate(pkgs):
            xs = [j + i * width for j in range(n_bars)]
            ys = [per_pkg.get(pkg, {}).get(k, 0) for k in keys]
            ax.bar(xs, ys, width=width, color=PACKAGE_COLORS[i % len(PACKAGE_COLORS)],
                   label=pkg)
        offset = (n_pkgs - 1) * width / 2
        ax.set_xticks([j + offset for j in range(n_bars)], keys, rotation=90, fontsize=8)
        ax.set_title(f"CT.gov — {dim}", fontsize=13)
        ax.set_ylabel("studies", fontsize=9)
        ax.legend(fontsize=8)
        fig.tight_layout()
        paths.append(_save(fig, out_dir, f"fig_ctgov_trends_{idx}.png"))
    return paths


def plot_cordis_funding(funding: pd.DataFrame, status: pd.DataFrame, out_dir: Path) -> Path:
    """fig_cordis_funding.png: two stacked panels — EUR per programme, projects per status.

    Portrait 2x1 stack (issue-48 F5) with y-axis labels (F7).
    """
    fund, programmes, stat, statuses, pkgs = _cordis_series(funding, status)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=FIGSIZE, dpi=DPI)
    xs = list(range(len(pkgs)))
    w1 = 0.86 / max(len(programmes), 1)
    for j, prog in enumerate(programmes):
        ys = [float(fund.get(p, {}).get(prog, (0, "0"))[1]) for p in pkgs]
        ax1.bar([x + j * w1 for x in xs], ys, width=w1,
                color=PACKAGE_COLORS[j % len(PACKAGE_COLORS)], label=prog)
    ax1.set_xticks([x + (len(programmes) - 1) * w1 / 2 for x in xs], pkgs, rotation=90, fontsize=8)
    ax1.set_title("EU contribution per programme (EUR)", fontsize=11)
    ax1.set_ylabel("EUR contribution", fontsize=9)
    ax1.ticklabel_format(axis="y", style="sci", scilimits=(9, 9))
    w2 = 0.86 / max(len(statuses), 1)
    for j, st in enumerate(statuses):
        ys = [stat.get(p, {}).get(st, 0) for p in pkgs]
        ax2.bar([x + j * w2 for x in xs], ys, width=w2,
                color=PACKAGE_COLORS[j % len(PACKAGE_COLORS)], label=st)
    ax2.set_xticks([x + (len(statuses) - 1) * w2 / 2 for x in xs], pkgs, rotation=90, fontsize=8)
    ax2.set_title("Projects per status", fontsize=11)
    ax2.set_ylabel("projects", fontsize=9)
    ax1.legend(fontsize=8)
    ax2.legend(fontsize=8)
    fig.suptitle("CORDIS funding per package", fontsize=13)
    fig.tight_layout()
    return _save(fig, out_dir, "fig_cordis_funding.png")


def plot_openalex_dimensions(df: pd.DataFrame, out_dir: Path) -> list[Path]:
    """fig_openalex_landscape_*.png: one full-size bar chart per OpenAlex dimension.

    Each dimension gets its own standalone portrait figure (no grid grouping)
    so labels, bars, and legends are fully readable. Files are numbered
    sequentially in dimension first-appearance order.
    """
    groups, pkgs = _openalex_topn(df)
    paths: list[Path] = []
    for idx, dim in enumerate(groups, start=1):
        fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
        keys, per_pkg = groups[dim]
        n_bars = len(keys)
        n_pkgs = len(pkgs)
        width = 0.86 / max(n_pkgs, 1)
        for i, pkg in enumerate(pkgs):
            xs = [j + i * width for j in range(n_bars)]
            ys = [per_pkg.get(pkg, {}).get(k, ("", 0))[1] for k in keys]
            ax.bar(xs, ys, width=width, color=PACKAGE_COLORS[i % len(PACKAGE_COLORS)],
                   label=pkg)
        labels = []
        for k in keys:
            disp = next((per_pkg[p][k][0] for p in pkgs if k in per_pkg.get(p, {})), k)
            labels.append(disp)
        offset = (n_pkgs - 1) * width / 2
        ax.set_xticks([j + offset for j in range(n_bars)], labels, rotation=90, fontsize=8)
        ax.set_title(f"OpenAlex — {dim}", fontsize=13)
        ax.set_ylabel("works", fontsize=9)
        ax.legend(fontsize=8)
        fig.tight_layout()
        paths.append(_save(fig, out_dir, f"fig_openalex_landscape_{idx}.png"))
    return paths


def plot_coverage(coverage: pd.DataFrame, overview: pd.DataFrame, out_dir: Path) -> Path:
    """fig_coverage.png: package × source evidence-role matrix + overview totals bars.

    v2 redesign (issue-53 D-03; the completeness heat-map is replaced by the
    declared-role matrix): cells are coloured by role (driver/context/n-a)
    and annotated with the record count; the overview panel is kept. Portrait
    2×1 stack (issue-48 F5) with the overview panel y-axis label (F7).
    """
    matrix, pkgs, srcs = _coverage_matrix(coverage)
    counts = _overview_counts(overview)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=FIGSIZE, dpi=DPI, height_ratios=[1.15, 1])
    for r, pkg in enumerate(pkgs):
        for c, src in enumerate(srcs):
            n, role = matrix.get(pkg, {}).get(src, (0, "n/a"))
            ax1.add_patch(Rectangle((c, r), 0.94, 0.94, facecolor=ROLE_COLORS[role],
                                    edgecolor="black", linewidth=0.5))
            ax1.text(c + 0.47, r + 0.47, str(n), ha="center", va="center",
                     fontsize=8, color=ROLE_TEXT[role])
    ax1.set_xlim(-0.05, len(srcs) - 0.01)
    ax1.set_ylim(len(pkgs) - 0.01, -0.05)
    ax1.set_xticks([c + 0.47 for c in range(len(srcs))], srcs, fontsize=8)
    ax1.set_yticks([r + 0.47 for r in range(len(pkgs))], pkgs, fontsize=8)
    ax1.set_title("Evidence role matrix (record counts)", fontsize=11)
    ax1.legend(handles=[Patch(facecolor=ROLE_COLORS[f], edgecolor="black", label=f)
                        for f in ROLES], fontsize=8, ncol=3,
               loc="upper center", bbox_to_anchor=(0.5, -0.14))
    w = 0.86 / len(OVERVIEW_COUNT_METRICS)
    for j, metric in enumerate(OVERVIEW_COUNT_METRICS):
        ys = [counts.get(p, {}).get(metric, 0) for p in pkgs]
        ax2.bar([x + j * w for x in range(len(pkgs))], ys, width=w,
                color=PACKAGE_COLORS[j % len(PACKAGE_COLORS)], label=metric)
    ax2.set_xticks([x + (len(OVERVIEW_COUNT_METRICS) - 1) * w / 2 for x in range(len(pkgs))],
                   pkgs, rotation=90, fontsize=8)
    ax2.set_title("Overview totals per package", fontsize=11)
    ax2.set_ylabel("record count", fontsize=9)
    ax2.legend(fontsize=8)
    fig.suptitle("Evidence roles and record counts per package and source", fontsize=13)
    fig.tight_layout()
    return _save(fig, out_dir, "fig_coverage.png")


def plot_openalex_trend_multiples(df: pd.DataFrame, out_dir: Path) -> Path:
    """fig_openalex_trend_multiples.png: one publication-year panel per package.

    Trend small-multiples (issue-53 WI-07 second cut-candidate, delivered):
    a 2×3 grid in config order, one bar panel per package showing the
    OpenAlex publication_year marginal (the only summable works marginal,
    2026-09-28 headline rule — nothing is summed across dimensions here).
    Plain-English title; axes labelled in works and year.
    """
    groups, pkgs = _openalex_topn(df, top_n=10**9)
    per_year = groups.get("publication_year")
    if per_year is None:
        raise ValueError("openalex_landscape.csv carries no publication_year dimension")
    keys, per_pkg = per_year
    fig, axes = plt.subplots(2, 3, figsize=FIGSIZE, dpi=DPI)
    for i, ax in enumerate(axes.flat):
        if i >= len(pkgs):  # fewer packages than panels: blank cell, kept layout
            ax.axis("off")
            continue
        pkg = pkgs[i]
        years = sorted((k for k in keys if k in per_pkg.get(pkg, {})), key=str)
        ys = [per_pkg[pkg][k][1] for k in years]
        ax.bar([str(k) for k in years], ys,
               color=PACKAGE_COLORS[i % len(PACKAGE_COLORS)])
        ax.set_title(pkg, fontsize=7)
        ax.tick_params(axis="x", labelsize=5, rotation=90)
        ax.tick_params(axis="y", labelsize=6)
        ax.set_ylabel("works", fontsize=6)
    fig.suptitle("Publications per year per package (OpenAlex; latest year partial)",
                 fontsize=10)
    fig.tight_layout()
    return _save(fig, out_dir, "fig_openalex_trend_multiples.png")


def load_na_packages(evidence_map_path: Path | str = EVIDENCE_MAP_PATH,
                     packages: list[str] | None = None) -> dict[str, set[str]]:
    """source -> set of packages whose declared role is n/a (issue-53 D-03).

    Self-contained duplicate of the evidence_map pattern (decisions.md
    2026-09-29 self-contained ruling; mirrors build_tables.load_evidence_roles
    by construction, never imports it). Snapshot-derived tables legitimately
    lack rows for these packages — documented, not pulled, never padded.
    Raises ValueError on an unknown source or a package outside config.
    """
    with open(evidence_map_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f) or []
    known = set(packages) if packages is not None else {p for p, _d, _x in
                                                        load_package_model()}
    out: dict[str, set[str]] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"evidence_map entry #{i} is not a map: {entry!r}")
        source = str(entry.get("source", ""))
        if source not in STEM_SOURCE.values():
            continue  # non-snapshot sources (none today) are inert here
        pkg = str(entry.get("package", ""))
        if pkg not in known:
            raise ValueError(f"evidence_map entry #{i}: package {pkg!r} not in config")
        if str(entry.get("role", "")) == "n/a":
            out.setdefault(source, set()).add(pkg)
    return out


def _validate_bindings(frames: dict[str, pd.DataFrame]) -> None:
    """Run every figure derive helper so all math is validated before any write."""
    _market_sizing_bands(frames["market_sizing"])
    _ctgov_groups(frames["ctgov_counts"])
    _cordis_series(frames["cordis_funding"], frames["cordis_status"])
    _openalex_topn(frames["openalex_landscape"])
    _coverage_matrix(frames["coverage"])
    _overview_counts(frames["overview"])


def main(*, processed_dir: Path | str | None = None,
         fig_dir: Path | str | None = None,
         gen_dir: Path | str | None = None,
         config_path: Path | str | None = None,
         evidence_map_path: Path | str | None = None) -> int:
    """Render 6 Markdown tables + PNGs from the processed CSVs (FR1). Returns 0.

    Keyword-only seam mirrors size_markets.main so check_figures.py runs
    this in-process over fixtures in tmp dirs; defaults are
    REPO_ROOT-anchored, so the script works from any cwd. All reads and
    all binding validation complete BEFORE any write — a bad row means
    zero output (validate-all-then-write-all, size_markets precedent).
    Outputs are idempotent in-place overwrites (FR6).
    """
    processed = _resolve(processed_dir, PROCESSED_DIR)
    figs = _resolve(fig_dir, FIG_DIR)
    gens = _resolve(gen_dir, GEN_DIR)
    cfg = _resolve(config_path, PACKAGES_PATH)
    emap = _resolve(evidence_map_path, EVIDENCE_MAP_PATH)

    packages = [name for name, _display_name, _depth in load_package_model(cfg)]
    # Issue-53 D-03: the pulled universe is the six renamed keys; a package ×
    # source declared n/a in config/evidence_map.yaml legitimately has no rows
    # in the snapshot-derived table (documented, not pulled, never padded).
    na_by_source = load_na_packages(emap, packages)
    frames: dict[str, pd.DataFrame] = {}
    for stem in LOCKED_SCHEMAS:
        source = STEM_SOURCE.get(stem)
        optional = na_by_source.get(source, set()) if source else set()
        frames[stem] = read_processed(processed / f"{stem}.csv", LOCKED_SCHEMAS[stem],
                                      packages, optional=optional)
    _validate_bindings(frames)

    for stem in TABLE_STEMS:
        write_table(frames[stem], gens, stem)
    plot_market_sizing(frames["market_sizing"], figs)
    ctgov_paths = plot_ctgov_dimensions(frames["ctgov_counts"], figs)
    plot_cordis_funding(frames["cordis_funding"], frames["cordis_status"], figs)
    oa_paths = plot_openalex_dimensions(frames["openalex_landscape"], figs)
    plot_coverage(frames["coverage"], frames["overview"], figs)
    plot_openalex_trend_multiples(frames["openalex_landscape"], figs)
    n_figs = len(FIGURES) + len(ctgov_paths) + len(oa_paths)
    print(f"wrote {len(TABLE_STEMS)} tables to {gens} and {n_figs} figures to {figs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
