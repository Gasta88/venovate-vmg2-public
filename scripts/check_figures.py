"""Ad-hoc verification gates for scripts/make_figures.py (issue-11).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_figures.py

Prints `G-<id> PASS|FAIL` per gate (G-0…G-7 with one check per figure
(G-4a…G-4e) and per table (G-5a…G-5f), plus G-INT integration over the REAL
committed data/processed CSVs + packages.yaml) and exits non-zero if any
gate fails. Zero network access; every write goes into per-gate
tempfile.TemporaryDirectory dirs — G-INT writes only to its tmp dir and
asserts the real report/figures/, report/generated/, data/processed/ and
report/*.md prose trees are untouched. Mirrors check_markets.py's runner
and fixture style; imports ONLY make_figures (self-contained scripts
ruling, decisions.md 2026-09-29).

Fixture packages are `zeta_pkg`/`alpha_pkg` — config order deliberately
DIFFERS from alphabetical so any accidental lexical sort fails the gates.
The fixture processed CSVs are internally consistent (marginal sums match
overview, coverage cells carry the locked driver/context/n-a roles of
issue-53 D-03 — the retired full/thin/empty completeness labels must not
appear anywhere in output — alpha's ctgov cell is a declared n/a with no
rows in the snapshot-derived tables, never padded) and the country
dimensions are built so max-ranking (the implemented, never-summed rule)
and sum-ranking would pick DIFFERENT top keys — catching any future
cross-package summing.

Determinism: tables are compared byte-wise (sha256) across two runs; PNGs
are compared PIXEL-wise via matplotlib.image.imread + numpy.array_equal
(cross-machine PNG byte-reproducibility is explicitly out per the
make_figures.py module docstring). The first run inside G-6 doubles as the
font-cache warm-up before the pixel compare.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_figures as mf  # noqa: E402

FIXTURE_PACKAGES = ("zeta_pkg", "alpha_pkg")  # config order != alphabetical

PACKAGES_YAML = (
    "packages:\n"
    "  - name: zeta_pkg\n"
    "    display_name: Zeta Package\n"
    "    depth: deep\n"
    "  - name: alpha_pkg\n"
    "    display_name: Alpha Package\n"
    "    depth: light\n"
)

# Fixture evidence_map (issue-53 D-03): alpha x ctgov is n/a — mirroring the
# real p2_insilico_workshop x ctgov omission; the fixture coverage table
# carries the roles the tables are built with.
EVIDENCE_MAP_YAML = """\
- {package: zeta_pkg, source: openalex, role: driver}
- {package: zeta_pkg, source: ctgov, role: context}
- {package: zeta_pkg, source: cordis, role: context}
- {package: alpha_pkg, source: openalex, role: context}
- {package: alpha_pkg, source: ctgov, role: n/a}
- {package: alpha_pkg, source: cordis, role: context}
"""


def write_evidence_map(tmp: Path) -> Path:
    cfg = tmp / "evidence_map.yaml"
    cfg.write_text(EVIDENCE_MAP_YAML, encoding="utf-8")
    return cfg

# Hand-consistent mini processed tables (same locked v2 schemas as the real
# CSVs — issue-53 D-03/D-05). zeta: 56 works (pub_year 30+26), 3 studies,
# 56 cordis projects; alpha: 6 works, 0 studies (ctgov role n/a — no rows,
# never padded), 1 cordis project. market_sizing v2 rows are quantile bands
# (p10/p50/p90 inside the bound-wise envelope_low/high). Country marginals
# deliberately exceed study/work totals (multi-country records) and are
# tuned so max-ranking and sum-ranking disagree on the top key:
# ctgov country Germany max 4 sum 4 vs United States max 3 sum 5;
# openalex country DE max 5 sum 9 vs US max 8 sum 9 (sum tie -> first
# appearance DE; max -> US).
FIXTURE_CSVS: dict[str, str] = {
    "market_sizing": (
        "package,layer,p10,p50,p90,envelope_low,envelope_high,unit,test_in_validation\n"
        "zeta_pkg,context,2500,5000,7500,2000,8000,EUR/year,n\n"
        "zeta_pkg,tam,1200,4000,8500,1000,9000,EUR/year,y\n"
        "zeta_pkg,som,600,2000,6000,500,8000,EUR/year,y\n"
        "alpha_pkg,som,120,300,700,100,900,EUR/year,n\n"
    ),
    "ctgov_counts": (
        "package,dimension,key,count\n"
        "zeta_pkg,country,Germany,4\n"
        "zeta_pkg,country,United States,2\n"
        "zeta_pkg,overall_status,COMPLETED,2\n"
        "zeta_pkg,overall_status,RECRUITING,1\n"
        "zeta_pkg,phase,PHASE2,2\n"
        "zeta_pkg,phase,NA,1\n"
    ),
    "cordis_funding": (
        "package,programme,projects,eu_contribution_eur\n"
        "zeta_pkg,H2020,34,1000.50\n"
        "zeta_pkg,HORIZON,22,2000.25\n"
        "alpha_pkg,H2020,1,0.00\n"
    ),
    "cordis_status": (
        "package,status,projects\n"
        "zeta_pkg,CLOSED,30\n"
        "zeta_pkg,SIGNED,20\n"
        "zeta_pkg,TERMINATED,6\n"
        "alpha_pkg,CLOSED,1\n"
    ),
    "openalex_landscape": (
        "package,dimension,key,key_display_name,count\n"
        "zeta_pkg,authorships.countries,https://openalex.org/countries/DE,Germany,5\n"
        "zeta_pkg,authorships.countries,https://openalex.org/countries/US,United States,1\n"
        "zeta_pkg,authorships.institutions.id,https://openalex.org/I100,Alpha Institute,1\n"
        "zeta_pkg,publication_year,2024,2024,30\n"
        "zeta_pkg,publication_year,2025,2025,26\n"
        "alpha_pkg,authorships.countries,https://openalex.org/countries/DE,Germany,4\n"
        "alpha_pkg,authorships.countries,https://openalex.org/countries/US,United States,8\n"
        "alpha_pkg,authorships.institutions.id,https://openalex.org/I200,Zeta University,3\n"
        "alpha_pkg,publication_year,2024,2024,6\n"
    ),
    "coverage": (
        "package,source,snapshot_date,record_count,role\n"
        "zeta_pkg,openalex,2026-09-28,56,driver\n"
        "zeta_pkg,ctgov,2026-09-28,3,context\n"
        "zeta_pkg,cordis,2026-09-28,56,context\n"
        "alpha_pkg,openalex,2026-09-28,6,context\n"
        "alpha_pkg,ctgov,2026-09-28,0,n/a\n"
        "alpha_pkg,cordis,2026-09-28,1,context\n"
    ),
    "overview": (
        "package,openalex_works,ctgov_studies,cordis_projects,cordis_eu_contribution_eur\n"
        "zeta_pkg,56,3,56,3000.75\n"
        "alpha_pkg,6,0,1,0.00\n"
    ),
}

# Committed fixture baseline for the v2 market_sizing table (issue-53
# D-04/D-05): one row per package x layer, quantile bands inside the
# bound-wise envelope; absent layers emit no row (never zero-filled).
FIXTURE_MARKET_SIZING_MD = (
    "| package | layer | p10 | p50 | p90 | envelope_low | envelope_high | unit "
    "| test_in_validation |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| zeta_pkg | context | 2500 | 5000 | 7500 | 2000 | 8000 | EUR/year | n |\n"
    "| zeta_pkg | tam | 1200 | 4000 | 8500 | 1000 | 9000 | EUR/year | y |\n"
    "| zeta_pkg | som | 600 | 2000 | 6000 | 500 | 8000 | EUR/year | y |\n"
    "| alpha_pkg | som | 120 | 300 | 700 | 100 | 900 | EUR/year | n |\n"
)


def write_processed(tmp: Path) -> tuple[Path, Path]:
    """Fresh fixture processed dir (all 7 CSVs) + non-alphabetical packages.yaml."""
    proc = tmp / "processed"
    proc.mkdir(exist_ok=True)
    for stem, text in FIXTURE_CSVS.items():
        (proc / f"{stem}.csv").write_text(text, encoding="utf-8")
    cfg = tmp / "packages.yaml"
    cfg.write_text(PACKAGES_YAML, encoding="utf-8")
    return proc, cfg


def fixture_frames(tmp: Path) -> dict[str, pd.DataFrame]:
    proc, _cfg = write_processed(tmp)
    em = write_evidence_map(tmp)
    na = mf.load_na_packages(em, list(FIXTURE_PACKAGES))
    frames: dict[str, pd.DataFrame] = {}
    for stem in mf.LOCKED_SCHEMAS:
        source = mf.STEM_SOURCE.get(stem)
        optional = na.get(source, set()) if source else set()
        frames[stem] = mf.read_processed(proc / f"{stem}.csv", mf.LOCKED_SCHEMAS[stem],
                                         list(FIXTURE_PACKAGES), optional=optional)
    return frames


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def _names(cfg: Path) -> list[str]:
    return [name for name, _display_name, _depth in mf.load_package_model(cfg)]


def _sha_map(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def _expect_valueerror(label: str, fn, needle: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert needle in str(exc), f"{label}: expected {needle!r} in {exc!r}"
    else:
        raise AssertionError(f"{label}: expected ValueError naming {needle!r}")


def _assert_png(path: Path, name: str) -> None:
    """G-4 shared check: the figure exists and decodes to a non-empty raster."""
    assert path.name == name and path.is_file(), f"missing {name}: {path}"
    arr = mpimg.imread(path)
    assert arr.ndim == 3 and arr.shape[0] > 0 and arr.shape[1] > 0, (name, arr.shape)
    assert arr.dtype in (np.float32, np.float64), f"{name}: PNG must decode to float [0,1]"


def _table_rows(gen_dir: Path, stem: str) -> tuple[list[str], list[str], list[list[str]]]:
    """Parse a generated GFM table into (header cells, separator cells, row cells)."""
    text = (gen_dir / f"{stem}.md").read_text("utf-8")
    assert text.endswith("\n") and not text.endswith("\n\n"), f"{stem}.md: single trailing newline"
    assert "\r" not in text, f"{stem}.md: LF-only line endings"
    lines = text.splitlines()
    split = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]  # noqa: E731
    return split(lines[0]), split(lines[1]), [split(ln) for ln in lines[2:]]


def _assert_cells_clean(stem: str, rows: list[list[str]]) -> None:
    """No NaN drift, no float-formatted integers, no scientific notation (G-7 idiom).

    Exact-cell checks, not substrings: real free-text cells legitimately
    contain "nan" (e.g. "Nanjing…") or digit+E ("PHASE1"), so only a cell
    that IS a NaN rendering or a numeric drift pattern may fail.
    """
    for row in rows:
        for cell in row:
            assert cell.lower() not in ("nan", "none", "nat"), \
                f"{stem}.md: NaN leaked into cell {cell!r}"
            assert not re.search(r"\d[eE][+-]?\d", cell), \
                f"{stem}.md: scientific notation in {cell!r}"
            assert not re.fullmatch(r"-?\d+\.0", cell), \
                f"{stem}.md: float drift in cell {cell!r}"


def _first_appearance(rows: list[list[str]], col: str, header: list[str]) -> list[str]:
    i = header.index(col)
    seen: list[str] = []
    for row in rows:
        if not seen or seen[-1] != row[i]:
            seen.append(row[i])
    return seen


# --- unit gates ----------------------------------------------------------------------------


def g0_no_forbidden_imports(tmp: Path) -> None:
    """AC/self-contained ruling: make_figures.py imports no sibling scripts and no
    network/env stack; Agg precedes pyplot; docstring carries contract + caveat."""
    content = (REPO_ROOT / "scripts" / "make_figures.py").read_text(encoding="utf-8")
    for banned in ("import _common", "from _common", "import build_tables",
                   "from build_tables", "import size_markets", "from size_markets",
                   "httpx", "requests", "dotenv", "os.environ", "socket", "urllib"):
        assert banned not in content, f"make_figures.py must not touch {banned!r}"
    assert "write_snapshot" not in content or "NOT reused" in content, \
        "make_figures.py must NOT call write_snapshot; only the 'NOT reused' doc is allowed"
    assert 'matplotlib.use("Agg")' in content, "Agg backend must be pinned explicitly"
    assert content.index('matplotlib.use("Agg")') < content.index("import matplotlib.pyplot"), \
        "matplotlib.use('Agg') must precede the pyplot import"
    # API documentation item: write-scope contract, inventory + cross-machine caveat.
    for needle in ("report/figures/", "report/generated/", "fig_market_sizing.png",
                   "fig_coverage.png", "byte-reproducibility", "font-cache"):
        assert needle in content, f"module docstring contract missing {needle!r}"


def g1_package_model(tmp: Path) -> None:
    cfg = _write(tmp, "packages.yaml", PACKAGES_YAML)
    model = mf.load_package_model(cfg)
    # config order preserved — NOT alphabetical (alpha_pkg would sort first)
    assert model == [("zeta_pkg", "Zeta Package", "deep"), ("alpha_pkg", "Alpha Package", "light")], model
    _expect_valueerror("empty", lambda: mf.load_package_model(
        _write(tmp, "empty.yaml", "segments: []\n")), "no package names")
    _expect_valueerror("missing depth", lambda: mf.load_package_model(_write(tmp, "d1.yaml",
        "packages:\n  - name: zeta_pkg\n")), "zeta_pkg")
    _expect_valueerror("missing name", lambda: mf.load_package_model(_write(tmp, "d2.yaml",
        "packages:\n  - depth: deep\n")), "missing `name`")
    _expect_valueerror("unknown depth", lambda: mf.load_package_model(_write(tmp, "d3.yaml",
        "packages:\n  - name: zeta_pkg\n    depth: medium\n")), "medium")


def g2_read_processed(tmp: Path) -> None:
    proc, _cfg = write_processed(tmp)
    names = list(FIXTURE_PACKAGES)
    na = mf.load_na_packages(write_evidence_map(tmp), names)
    for stem, _text in FIXTURE_CSVS.items():
        source = mf.STEM_SOURCE.get(stem)
        optional = na.get(source, set()) if source else set()
        df = mf.read_processed(proc / f"{stem}.csv", mf.LOCKED_SCHEMAS[stem], names,
                               optional=optional)
        assert list(df.columns) == mf.LOCKED_SCHEMAS[stem]
        assert all(str(t) == "str" for t in df.dtypes), dict(df.dtypes)  # dtype=str pattern
    # v2 market_sizing: all cells filled (no light-row empties in the new
    # schema); layer column carries the locked vocabulary, rows in source order
    ms = mf.read_processed(proc / "market_sizing.csv", mf.LOCKED_SCHEMAS["market_sizing"], names)
    assert set(ms["layer"]) <= {"context", "tam", "som"}
    assert list(ms["layer"]) == ["context", "tam", "som", "som"], list(ms["layer"])

    # `optional` packages (issue-53 D-03 n/a cells): a table may legitimately
    # lack rows for a declared-n/a package — but never for a pulled one.
    zeta_only_ctgov = "package,dimension,key,count\nzeta_pkg,overall_status,COMPLETED,2\n"
    df_opt = mf.read_processed(_write(tmp, "opt.csv", zeta_only_ctgov),
                               mf.LOCKED_SCHEMAS["ctgov_counts"], names,
                               optional={"alpha_pkg"})
    assert list(df_opt["package"]) == ["zeta_pkg"]
    _expect_valueerror("missing pulled package", lambda: mf.read_processed(
        _write(tmp, "opt2.csv", zeta_only_ctgov), mf.LOCKED_SCHEMAS["ctgov_counts"], names),
        "config order")

    reordered = mf.LOCKED_SCHEMAS["overview"][::-1]
    bad_header = _write(tmp, "h.csv", ",".join(reordered) + "\n")
    _expect_valueerror("header", lambda: mf.read_processed(
        bad_header, mf.LOCKED_SCHEMAS["overview"], names), "header")

    header_only = _write(tmp, "header_only.csv", ",".join(mf.LOCKED_SCHEMAS["overview"]) + "\n")
    _expect_valueerror("header-only", lambda: mf.read_processed(
        header_only, mf.LOCKED_SCHEMAS["overview"], names), "config order")

    ghost = FIXTURE_CSVS["market_sizing"].replace("alpha_pkg", "ghost_pkg")
    _expect_valueerror("unknown package", lambda: mf.read_processed(
        _write(tmp, "ghost.csv", ghost), mf.LOCKED_SCHEMAS["market_sizing"], names), "ghost_pkg")

    missing = FIXTURE_CSVS["market_sizing"].splitlines()
    missing = "\n".join(missing[:2]) + "\n"  # header + zeta context only
    _expect_valueerror("missing package", lambda: mf.read_processed(
        _write(tmp, "missing.csv", missing), mf.LOCKED_SCHEMAS["market_sizing"], names),
        "config order")

    interleaved = (
        "package,dimension,key,count\n"
        "zeta_pkg,overall_status,COMPLETED,2\n"
        "alpha_pkg,overall_status,COMPLETED,3\n"
        "zeta_pkg,phase,PHASE2,2\n"
    )
    _expect_valueerror("non-contiguous", lambda: mf.read_processed(
        _write(tmp, "inter.csv", interleaved), mf.LOCKED_SCHEMAS["ctgov_counts"], names),
        "config order")


def g3_write_table(tmp: Path) -> None:
    frames = fixture_frames(tmp)
    out = tmp / "md"
    path = mf.write_table(frames["market_sizing"], out, "market_sizing")
    assert path == out / "market_sizing.md"
    text = path.read_text("utf-8")
    # full locked contract, byte for byte: header order verbatim, rows in
    # source order, light-row empties, single trailing LF, no CR
    assert text == FIXTURE_MARKET_SIZING_MD, f"write_table contract drift:\n{text!r}"
    # pipe escaping + verbatim cell text (leading/trailing spaces kept)
    pipes = pd.DataFrame([{"package": "a|b", "note": "x | y"}]).astype(str)
    esc = mf.write_table(pipes, out, "pipes").read_text("utf-8")
    assert "| a\\|b | x \\| y |" in esc, esc
    # utf-8 round trip
    uni = pd.DataFrame([{"package": "zeta_pkg", "note": "Venise \u00c9cole"}]).astype(str)
    upath = mf.write_table(uni, out, "unicode")
    assert "\u00c9cole" in upath.read_bytes().decode("utf-8")
    # plain overwrite: same bytes, no dated dirs, no manifests, no extra files
    again = mf.write_table(frames["market_sizing"], out, "market_sizing")
    assert again.read_text("utf-8") == FIXTURE_MARKET_SIZING_MD
    assert sorted(p.name for p in out.iterdir()) == ["market_sizing.md", "pipes.md", "unicode.md"]


# --- G-4: one gate per figure — schema + math + PNG decode --------------------------------


def g4a_fig_market_sizing(tmp: Path) -> None:
    frames = fixture_frames(tmp)
    bands = mf._market_sizing_bands(frames["market_sizing"])
    # band endpoints == the CSV quantile/envelope cells, config row order (v2)
    assert bands == [
        ("zeta_pkg", "context", 2500, 5000, 7500, 2000, 8000),
        ("zeta_pkg", "tam", 1200, 4000, 8500, 1000, 9000),
        ("zeta_pkg", "som", 600, 2000, 6000, 500, 8000),
        ("alpha_pkg", "som", 120, 300, 700, 100, 900),
    ], bands
    _assert_png(mf.plot_market_sizing(frames["market_sizing"], tmp / "fig"),
                "fig_market_sizing.png")
    bad = frames["market_sizing"].copy()
    bad.loc[bad["package"] == "zeta_pkg", "p10"] = "9000"  # p10 > p50: inverted band
    _expect_valueerror("inverted band", lambda: mf._market_sizing_bands(bad), "p10 <= p50 <= p90")
    drift = frames["market_sizing"].copy()
    drift.loc[drift["package"] == "zeta_pkg", "p90"] = "99999"  # outside the envelope
    _expect_valueerror("quantile outside envelope",
                       lambda: mf._market_sizing_bands(drift), "envelope")


def g4b_fig_ctgov_trends(tmp: Path) -> None:
    frames = fixture_frames(tmp)
    groups, pkgs = mf._ctgov_groups(frames["ctgov_counts"])
    # alpha x ctgov is declared n/a (fixture evidence_map): documented, not
    # pulled — the table has no alpha rows, never a padded zero row.
    assert pkgs == ["zeta_pkg"], pkgs
    assert list(groups) == ["country", "overall_status", "phase"]  # first-appearance order
    keys, per = groups["overall_status"]
    assert keys == ["COMPLETED", "RECRUITING"]
    assert per["zeta_pkg"] == {"COMPLETED": 2, "RECRUITING": 1}
    assert "alpha_pkg" not in per  # n/a: no rows, not zeros
    # math: each single-valued marginal totals the package's study count
    ov = mf._overview_counts(frames["overview"])
    for pkg in pkgs:
        for dim in ("overall_status", "phase"):
            assert sum(groups[dim][1].get(pkg, {}).values()) == ov[pkg]["ctgov_studies"], (pkg, dim)
    assert ov["alpha_pkg"]["ctgov_studies"] == 0  # n/a surfaces as an honest 0 total
    # ranking is by MAX, never summed across packages (the discriminating
    # max-vs-sum fixture lives in g4d's openalex countries; here Germany 4
    # beats United States 2 on both readings and stays the top key)
    top1, _ = mf._ctgov_groups(frames["ctgov_counts"], top_n=1)
    assert top1["country"][0] == ["Germany"], top1["country"][0]
    paths = mf.plot_ctgov_dimensions(frames["ctgov_counts"], tmp / "fig")
    assert len(paths) == 3  # one PNG per dimension
    for p in paths:
        _assert_png(p, p.name)
    bad = frames["ctgov_counts"].copy()
    bad.loc[bad.index[0], "count"] = "ten"
    _expect_valueerror("non-integer count", lambda: mf._ctgov_groups(bad), "non-integer")


def g4c_fig_cordis_funding(tmp: Path) -> None:
    frames = fixture_frames(tmp)
    fund, programmes, stat, statuses, pkgs = mf._cordis_series(
        frames["cordis_funding"], frames["cordis_status"])
    assert programmes == ["H2020", "HORIZON"] and statuses == ["CLOSED", "SIGNED", "TERMINATED"]
    assert pkgs == list(FIXTURE_PACKAGES)
    # EUR cell stays verbatim in the binding (no float re-formatting)
    assert fund["zeta_pkg"]["H2020"] == (34, "1000.50"), fund
    assert "HORIZON" not in fund["alpha_pkg"]  # missing combo is fine — plotted as 0
    assert stat["zeta_pkg"] == {"CLOSED": 30, "SIGNED": 20, "TERMINATED": 6}
    ov = mf._overview_counts(frames["overview"])
    for pkg in pkgs:
        assert sum(n for n, _eur in fund[pkg].values()) == ov[pkg]["cordis_projects"]
    _assert_png(mf.plot_cordis_funding(frames["cordis_funding"], frames["cordis_status"], tmp / "fig"),
                "fig_cordis_funding.png")
    bad = frames["cordis_funding"].copy()
    bad.loc[bad.index[0], "eu_contribution_eur"] = "1,000.50"
    _expect_valueerror("bad EUR", lambda: mf._cordis_series(bad, frames["cordis_status"]),
                       "non-numeric")


def g4d_fig_openalex_landscape(tmp: Path) -> None:
    frames = fixture_frames(tmp)
    groups, pkgs = mf._openalex_topn(frames["openalex_landscape"])
    assert pkgs == list(FIXTURE_PACKAGES)
    assert list(groups) == ["authorships.countries", "authorships.institutions.id",
                            "publication_year"]
    keys, per = groups["authorships.countries"]
    # max-ranking, never sum-ranking: DE and US both sum to 9 across
    # packages, but US max 8 > DE max 5 — a sum ranking would keep
    # first-appearance DE on the tie.
    assert keys == ["https://openalex.org/countries/US", "https://openalex.org/countries/DE"], keys
    assert per["zeta_pkg"]["https://openalex.org/countries/DE"] == ("Germany", 5)
    # publication_year is the ONLY marginal that totals works (2026-09-28
    # headline rule) — gate-side math, the figure itself sums nothing.
    _ykeys, yper = groups["publication_year"]
    ov = mf._overview_counts(frames["overview"])
    for pkg in pkgs:
        assert sum(c for _d, c in yper.get(pkg, {}).values()) == ov[pkg]["openalex_works"]
    paths = mf.plot_openalex_dimensions(frames["openalex_landscape"], tmp / "fig")
    assert len(paths) == 3  # one PNG per dimension
    for p in paths:
        _assert_png(p, p.name)
    dup = frames["openalex_landscape"]
    dup = pd.concat([dup, dup.iloc[[0]]], ignore_index=True)  # same package/dim/key twice
    _expect_valueerror("duplicate key", lambda: mf._openalex_topn(dup), "duplicate")


def g4e_fig_coverage(tmp: Path) -> None:
    frames = fixture_frames(tmp)
    matrix, mpkgs, srcs = mf._coverage_matrix(frames["coverage"])
    assert mpkgs == list(FIXTURE_PACKAGES) and srcs == ["openalex", "ctgov", "cordis"]
    # cell roles == the coverage.csv role cells (v2, issue-53 D-03)
    assert matrix["zeta_pkg"]["ctgov"] == (3, "context")
    assert matrix["alpha_pkg"]["ctgov"] == (0, "n/a")  # zero-row n/a kept, not padded
    counts = mf._overview_counts(frames["overview"])
    srcmap = {"openalex": "openalex_works", "ctgov": "ctgov_studies", "cordis": "cordis_projects"}
    for pkg in mpkgs:
        for src, (n, role) in matrix[pkg].items():
            assert n == counts[pkg][srcmap[src]], (pkg, src)  # counts agree with overview.csv
            assert role in mf.ROLES, (pkg, src, role)
    _assert_png(mf.plot_coverage(frames["coverage"], frames["overview"], tmp / "fig"),
                "fig_coverage.png")
    bad = frames["coverage"].copy()
    bad.loc[bad.index[0], "role"] = "full"  # retired completeness label must fail
    _expect_valueerror("retired label", lambda: mf._coverage_matrix(bad), "role")
    bad2 = frames["coverage"].copy()
    bad2.loc[bad2.index[0], "role"] = "partial"
    _expect_valueerror("bad role", lambda: mf._coverage_matrix(bad2), "role")


# --- G-5: one gate per table — inventory/order/numeric-drift over fixture main() -----------


def _fixture_tables(tmp: Path) -> Path:
    proc, cfg = write_processed(tmp)
    em = write_evidence_map(tmp)
    out_g = tmp / "gen"
    assert mf.main(processed_dir=proc, fig_dir=tmp / "fig", gen_dir=out_g,
                   config_path=cfg, evidence_map_path=em) == 0
    return out_g


def _check_table_contract(tmp: Path, stem: str) -> tuple[list[str], list[list[str]]]:
    out_g = _fixture_tables(tmp)
    header, sep, rows = _table_rows(out_g, stem)
    assert header == mf.LOCKED_SCHEMAS[stem], (stem, header)  # source CSV header order
    assert sep == ["---"] * len(header), (stem, sep)
    # n/a cells (fixture: alpha x ctgov) legitimately lack rows — the expected
    # package order is config minus the source's n/a packages (issue-53 D-03).
    na = mf.load_na_packages(write_evidence_map(tmp), list(FIXTURE_PACKAGES))
    source = mf.STEM_SOURCE.get(stem)
    expected = [p for p in FIXTURE_PACKAGES if p not in na.get(source or "", set())]
    assert _first_appearance(rows, "package", header) == expected, \
        f"{stem}.md: package order != config order (lexical sort?)"
    _assert_cells_clean(stem, rows)
    return header, rows


def g5a_table_market_sizing(tmp: Path) -> None:
    header, rows = _check_table_contract(tmp, "market_sizing")
    assert len(rows) == 4  # zeta context/tam/som + alpha som (absent layers: no row)
    alpha_som = rows[3]
    assert alpha_som[header.index("layer")] == "som"
    assert alpha_som[header.index("unit")] == "EUR/year"
    assert alpha_som[header.index("test_in_validation")] == "n"
    # quantile cells render as plain integers, envelope columns present (v2)
    for col in ("p10", "p50", "p90", "envelope_low", "envelope_high"):
        assert alpha_som[header.index(col)].isdigit(), (col, alpha_som)


def g5b_table_ctgov_counts(tmp: Path) -> None:
    _header, rows = _check_table_contract(tmp, "ctgov_counts")
    assert len(rows) == 6  # every source row present, none dropped (zeta only: alpha is n/a)
    assert rows[0][2] == "Germany" and rows[0][3] == "4"  # verbatim, source order


def g5c_table_cordis_funding(tmp: Path) -> None:
    _header, rows = _check_table_contract(tmp, "cordis_funding")
    assert len(rows) == 3
    # 2-decimal EUR strings render unchanged (no float re-formatting)
    assert rows[0][3] == "1000.50" and rows[2][3] == "0.00", rows


def g5d_table_openalex_landscape(tmp: Path) -> None:
    _header, rows = _check_table_contract(tmp, "openalex_landscape")
    assert len(rows) == 9
    assert rows[0][3] == "Germany"  # display names verbatim
    assert rows[0][2] == "https://openalex.org/countries/DE"


def g5e_table_coverage(tmp: Path) -> None:
    _header, rows = _check_table_contract(tmp, "coverage")
    assert len(rows) == 6
    roles = {r[1]: r[4] for r in rows if r[0] == "alpha_pkg"}
    assert roles == {"openalex": "context", "ctgov": "n/a", "cordis": "context"}, roles
    # retired completeness labels must not render anywhere in the table
    assert all(r[4] in ("driver", "context", "n/a") for r in rows), rows


def g5f_table_overview(tmp: Path) -> None:
    _header, rows = _check_table_contract(tmp, "overview")
    assert len(rows) == 2
    assert rows[0] == ["zeta_pkg", "56", "3", "56", "3000.75"], rows[0]
    assert rows[1][4] == "0.00"  # never "0.0" or "0"


# --- integration gates ----------------------------------------------------------------------


def g6_determinism(tmp: Path) -> None:
    """Two runs into separate dirs: tables byte-identical, PNGs pixel-identical;
    third run in place: same bytes, no new files (idempotent overwrite)."""
    proc, cfg = write_processed(tmp)
    em = write_evidence_map(tmp)
    a_f, a_g, b_f, b_g = tmp / "a_f", tmp / "a_g", tmp / "b_f", tmp / "b_g"
    assert mf.main(processed_dir=proc, fig_dir=a_f, gen_dir=a_g, config_path=cfg,
                   evidence_map_path=em) == 0
    assert mf.main(processed_dir=proc, fig_dir=b_f, gen_dir=b_g, config_path=cfg,
                   evidence_map_path=em) == 0
    sha_a, sha_b = _sha_map(a_g), _sha_map(b_g)
    assert set(sha_a) == {f"{s}.md" for s in mf.TABLE_STEMS} == set(sha_b)
    assert sha_a == sha_b, "two runs into separate dirs must be byte-identical tables"
    for name in mf.FIGURES:  # pixel-level, not byte-level (module docstring caveat)
        assert np.array_equal(mpimg.imread(a_f / name), mpimg.imread(b_f / name)), \
            f"{name}: pixels drifted between runs"
    before = _sha_map(a_f) | _sha_map(a_g)
    assert mf.main(processed_dir=proc, fig_dir=a_f, gen_dir=a_g, config_path=cfg,
                   evidence_map_path=em) == 0
    assert (_sha_map(a_f) | _sha_map(a_g)) == before, "in-place rerun must not churn bytes or files"


def g7_end_to_end(tmp: Path) -> None:
    """Fixture run into fresh nonexistent dirs: exact inventory, baseline bytes."""
    proc, cfg = write_processed(tmp)
    em = write_evidence_map(tmp)
    out_f, out_g = tmp / "figs", tmp / "gen"  # do not exist yet -> must be created
    assert mf.main(processed_dir=proc, fig_dir=out_f, gen_dir=out_g, config_path=cfg,
                   evidence_map_path=em) == 0
    names = sorted(p.name for p in out_f.iterdir())
    # Base figures + per-dimension figures (3 CT.gov + 3 OpenAlex for the fixture)
    assert all(n in names for n in mf.FIGURES), "missing base figure"
    for i in range(1, 4):
        assert f"fig_ctgov_trends_{i}.png" in names, f"missing ctgov dim {i}"
        assert f"fig_openalex_landscape_{i}.png" in names, f"missing oa dim {i}"
    assert sorted(p.name for p in out_g.iterdir()) == sorted(f"{s}.md" for s in mf.TABLE_STEMS)
    assert all(p.is_file() for p in out_f.rglob("*")) and all(p.is_file() for p in out_g.rglob("*"))
    assert not list(out_g.glob("*.json")) and not list(out_f.glob("*.json"))  # no manifests
    assert (out_g / "market_sizing.md").read_text("utf-8") == FIXTURE_MARKET_SIZING_MD
    for stem in mf.TABLE_STEMS:  # no NaN/float/scientific drift in any table
        _header, _sep, rows = _table_rows(out_g, stem)
        _assert_cells_clean(stem, rows)


def g_int_real_data(tmp: Path) -> None:
    """Offline smoke over the REAL committed CSVs + config; real trees untouched."""
    figs_real = REPO_ROOT / "report" / "figures"
    gen_real = REPO_ROOT / "report" / "generated"
    proc_real = REPO_ROOT / "data" / "processed"
    prose = sorted((REPO_ROOT / "report").glob("*.md"))

    def sha_files(paths):
        return {str(p.relative_to(REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths}

    before = {"fig": _sha_map(figs_real), "gen": _sha_map(gen_real),
              "proc": _sha_map(proc_real), "prose": sha_files(prose)}
    # committed inventory: base figures + per-dimension figures + README
    expected_fig_names = set(mf.FIGURES) | {"README.md"}
    figs_dir = sorted(p.name for p in figs_real.iterdir())
    assert all(n in figs_dir for n in expected_fig_names), "missing committed artifact"
    # issue-53 D-11: numbers.yaml joins the committed generated inventory
    assert set(before["gen"]) == {f"{s}.md" for s in mf.TABLE_STEMS} | {"README.md",
                                                                        "numbers.yaml"}

    out_f, out_g = tmp / "f", tmp / "g"
    assert mf.main(fig_dir=out_f, gen_dir=out_g) == 0  # production processed/config defaults

    after = {"fig": _sha_map(figs_real), "gen": _sha_map(gen_real),
             "proc": _sha_map(proc_real), "prose": sha_files(prose)}
    assert before == after, "G-INT must not touch the real report/, data/processed/ or prose trees"

    # regenerated output == committed artifacts (tables bytes, figures pixels)
    for stem in mf.TABLE_STEMS:
        assert (out_g / f"{stem}.md").read_bytes() == (gen_real / f"{stem}.md").read_bytes(), stem
    for name in mf.FIGURES:
        assert np.array_equal(mpimg.imread(out_f / name), mpimg.imread(figs_real / name)), name
    # per-dimension figures: pixel match for each generated file
    for name in (out_f.iterdir()):
        if name.name.startswith(("fig_ctgov_trends_", "fig_openalex_landscape_")):
            assert np.array_equal(mpimg.imread(out_f / name.name), mpimg.imread(figs_real / name.name)), name.name

    pkgs = _names(mf.PACKAGES_PATH)
    assert len(pkgs) == 6
    # issue-53 D-03: n/a cells (e.g. p2_insilico_workshop x ctgov) legitimately
    # lack rows in the snapshot-derived tables — documented, not pulled.
    na_by_source = mf.load_na_packages(mf.EVIDENCE_MAP_PATH, pkgs)
    for stem in mf.TABLE_STEMS:  # row order == real config package order
        header, _sep, rows = _table_rows(out_g, stem)
        config_pkgs_in_table = _first_appearance(rows, "package", header)
        source = mf.STEM_SOURCE.get(stem)
        expected = [p for p in pkgs if p not in na_by_source.get(source or "", set())]
        assert config_pkgs_in_table == expected, (stem, config_pkgs_in_table, expected)
        _assert_cells_clean(stem, rows)

    # cross-table math on the real committed data (headline rules)
    frames: dict[str, pd.DataFrame] = {}
    for stem in mf.LOCKED_SCHEMAS:
        source = mf.STEM_SOURCE.get(stem)
        optional = na_by_source.get(source, set()) if source else set()
        frames[stem] = mf.read_processed(proc_real / f"{stem}.csv", mf.LOCKED_SCHEMAS[stem],
                                         pkgs, optional=optional)
    ov = mf._overview_counts(frames["overview"])
    ct_groups, _ = mf._ctgov_groups(frames["ctgov_counts"])
    for pkg in pkgs:  # n/a packages sum to zero (no rows) — never skipped, never padded
        for dim in ("overall_status", "phase"):  # country never totals studies (multi-country)
            assert sum(ct_groups[dim][1].get(pkg, {}).values()) == ov[pkg]["ctgov_studies"], (pkg, dim)
    oa_groups, _ = mf._openalex_topn(frames["openalex_landscape"], top_n=10**9)
    for pkg in pkgs:  # publication_year marginal == works (the only summable one)
        assert sum(c for _d, c in oa_groups["publication_year"][1].get(pkg, {}).values()) \
            == ov[pkg]["openalex_works"], pkg
    fund, _pro, stat, _st, _pk = mf._cordis_series(frames["cordis_funding"], frames["cordis_status"])
    for pkg in pkgs:
        assert sum(n for n, _e in fund[pkg].values()) == ov[pkg]["cordis_projects"], pkg
        real_eur = frames["overview"].loc[frames["overview"]["package"] == pkg,
                                          "cordis_eu_contribution_eur"].iloc[0]
        assert abs(round(sum(float(e) for _n, e in fund[pkg].values()), 2) - float(real_eur)) < 0.005, \
            (pkg, real_eur)
    matrix, _mp, _sr = mf._coverage_matrix(frames["coverage"])
    srcmap = {"openalex": "openalex_works", "ctgov": "ctgov_studies", "cordis": "cordis_projects"}
    for pkg in pkgs:
        for src, (n, _role) in matrix[pkg].items():
            assert n == ov[pkg][srcmap[src]], (pkg, src)
    # committed-data baseline: the v2 sizing figure binding (quantile bands)
    bands = mf._market_sizing_bands(frames["market_sizing"])
    assert len(bands) == 15, len(bands)  # 3 deep x 3 layers + 3 light x 2 layers
    assert [b[:2] for b in bands[:2]] == [("p1_venospatial", "tam"), ("p1_venospatial", "som")]
    # retired completeness labels appear nowhere in the regenerated artifacts
    blob = "".join((out_g / f"{s}.md").read_text("utf-8") for s in mf.TABLE_STEMS)
    for banned in ("| full |", "| thin |", "| empty |"):
        assert banned not in blob, banned


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("G-0", g0_no_forbidden_imports),
        ("G-1", g1_package_model),
        ("G-2", g2_read_processed),
        ("G-3", g3_write_table),
        ("G-4a", g4a_fig_market_sizing),
        ("G-4b", g4b_fig_ctgov_trends),
        ("G-4c", g4c_fig_cordis_funding),
        ("G-4d", g4d_fig_openalex_landscape),
        ("G-4e", g4e_fig_coverage),
        ("G-5a", g5a_table_market_sizing),
        ("G-5b", g5b_table_ctgov_counts),
        ("G-5c", g5c_table_cordis_funding),
        ("G-5d", g5d_table_openalex_landscape),
        ("G-5e", g5e_table_coverage),
        ("G-5f", g5f_table_overview),
        ("G-6", g6_determinism),
        ("G-7", g7_end_to_end),
        ("G-INT", g_int_real_data),
    ):
        with tempfile.TemporaryDirectory(prefix="check_figures_") as td:
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
