"""build_tables: data/raw snapshots -> data/processed aggregate CSV tables (issue 9;
v2 roles per issue-53 D-03).

Reads the latest YYYY-MM-DD snapshot dir per source (openalex, ctgov, cordis)
under data/raw/, projects the config/packages.yaml packages onto compatible
frames keyed by `package`, and writes six small deterministic CSVs to
data/processed/ so the report builds without re-pulling:

  openalex_landscape.csv  package,dimension,key,key_display_name,count
  ctgov_counts.csv        package,dimension,key,count
  cordis_funding.csv      package,programme,projects,eu_contribution_eur
  cordis_status.csv       package,status,projects
  overview.csv            package,openalex_works,ctgov_studies,cordis_projects,cordis_eu_contribution_eur
  coverage.csv            package,source,snapshot_date,record_count,role

Contract (decisions.md 2026-09-28 "processed-tables contract + byte-determinism
rules", coverage-label clause superseded 2026-10-05 by issue-53 D-03): package
names and order come from config (never hard-coded; the six renamed keys of
D-01); every table keeps all packages as rows or zero cells — rows are never
dropped and gaps surface as 0 cells, never padded. Coverage carries a declared
ROLE per package × source resolved from config/evidence_map.yaml
(`driver | context | n/a`); the completeness labels full/thin/empty are
retired. An `n/a` cell (documented, not pulled — e.g. p2_insilico_workshop ×
ctgov) carries record_count 0 and role n/a honestly. OpenAlex works totals
come from the publication_year marginal only; CORDIS eu_contribution is
comma-decimal-normalized BEFORE summing and written at fixed 2-decimal format;
every write is byte-deterministic (index=False, utf-8, lineterminator="\\n",
stable column order, rows sorted by package then group key) so re-runs are
no-ops. _common.write_snapshot is deliberately NOT reused — dated dirs,
FileExistsError semantics and manifests fit raw pulls, not stable committed
tables (AC11). Fully offline: no network, no timestamps beyond the snapshot
date already on disk.

Run: uv run python scripts/build_tables.py
Gates: uv run python scripts/check_tables.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
PACKAGES_PATH = REPO_ROOT / "config" / "packages.yaml"
EVIDENCE_MAP_PATH = REPO_ROOT / "config" / "evidence_map.yaml"

# Exactly 3 sources — Lens removed (decisions.md 2026-09-28); locked display
# order for coverage.csv rows.
SOURCES = ("openalex", "ctgov", "cordis")

# Evidence roles locked by issue-53 D-03 (replaces the retired 0=empty /
# 1-49=thin / >=50=full completeness labels).
ROLES = ("driver", "context", "n/a")

# Rank used when one package × source carries several dimension entries:
# the most load-bearing role wins (driver > context > n/a).
_ROLE_RANK = {"driver": 0, "context": 1, "n/a": 2}

# OpenAlex dims locked by the 2026-09-26 aggregation-snapshot contract.
OPENALEX_DIMS = ("publication_year", "authorships.countries", "authorships.institutions.id")

# CORDIS datasets locked by the 2026-09-28 snapshot contract (stem suffixes).
CORDIS_DATASETS = ("h2020", "he")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve(path: Path | str | None, default: Path) -> Path:
    """Explicit path as given (relative -> cwd), else the repo-root default."""
    return default if path is None else Path(path)


def load_packages(config_path: Path | str = PACKAGES_PATH) -> list[str]:
    """Ordered `name:` values under top-level `packages:` (packages.yaml lock).

    Raises ValueError when the list is missing/empty — the pipeline is
    config-driven, never hard-coded (AC3/FR2).
    """
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    packages = [str(entry["name"]) for entry in data.get("packages") or []]
    if not packages:
        raise ValueError(f"no package names found under `packages:` in {config_path}")
    return packages


def load_evidence_roles(evidence_map_path: Path | str = EVIDENCE_MAP_PATH,
                        packages: list[str] | None = None) -> dict[tuple[str, str], str]:
    """(package, source) -> role from config/evidence_map.yaml (issue-53 D-03).

    One package × source may carry several dimension entries; the most
    load-bearing role wins (driver > context > n/a). Raises ValueError naming
    the offender on: a role outside ROLES, a source outside SOURCES, a package
    not in config, or a config package × source cell with NO declared role
    (every evidence count carries a declared role — AC-4 precondition).
    """
    with open(evidence_map_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f) or []
    if not isinstance(entries, list):
        raise ValueError(f"{evidence_map_path} must be a list of role declarations")
    known = set(packages) if packages is not None else set(load_packages())
    best: dict[tuple[str, str], str] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"evidence_map entry #{i} is not a map: {entry!r}")
        pkg = str(entry.get("package", ""))
        source = str(entry.get("source", ""))
        role = str(entry.get("role", ""))
        where = f"evidence_map entry #{i} ({pkg}/{source})"
        if pkg not in known:
            raise ValueError(f"{where}: package {pkg!r} is not in config/packages.yaml")
        if source not in SOURCES:
            raise ValueError(f"{where}: source {source!r} not in {list(SOURCES)}")
        if role not in ROLES:
            raise ValueError(f"{where}: role {role!r} not in {list(ROLES)}")
        key = (pkg, source)
        if key not in best or _ROLE_RANK[role] < _ROLE_RANK[best[key]]:
            best[key] = role
    for pkg in sorted(known):
        for source in SOURCES:
            if (pkg, source) not in best:
                raise ValueError(
                    f"{pkg} × {source} has no role in {evidence_map_path} — "
                    f"every evidence count carries a declared role (D-03)"
                )
    return best


def latest_snapshot_dir(base_dir: Path, source: str) -> Path:
    """Latest YYYY-MM-DD subdir under base_dir/<source>/ (FR1).

    Non-date entries (README.md, .gitkeep, …) are ignored. Raises
    FileNotFoundError naming the source when the dir is absent or holds no
    date dir — a missing snapshot fails loudly, never silently partial (AC9).
    """
    source_dir = Path(base_dir) / source
    dates = (
        sorted(p.name for p in source_dir.iterdir() if DATE_RE.fullmatch(p.name))
        if source_dir.is_dir()
        else []
    )
    if not dates:
        raise FileNotFoundError(
            f"source {source!r}: no {DATE_RE.pattern} snapshot dir under {source_dir} "
            f"— run the {source} pull first"
        )
    return source_dir / dates[-1]


def _sorted_by_package(df: pd.DataFrame, packages: list[str], by: list[str]) -> pd.DataFrame:
    """Lock package order to config order (ordered categorical, not alphabetical)
    and sort on the full stable key so no ties can reorder re-runs (AC10)."""
    out = df.copy()
    out["package"] = pd.Categorical(out["package"], categories=packages, ordered=True)
    return out.sort_values(by, kind="mergesort").reset_index(drop=True)


def write_table(df: pd.DataFrame, out_dir: Path, stem: str) -> Path:
    """Write one processed table under the byte-determinism contract (AC2/AC10/FR7).

    Plain overwrite (idempotent), no dated dirs, no manifests — this is why
    _common.write_snapshot is NOT reused here (AC11).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.csv"
    df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


def derive_openalex_landscape(snapshot_dir: Path, packages: list[str]) -> pd.DataFrame:
    """Long-form OpenAlex marginals: one row per package × dimension × group key (FR3/AC6).

    Reads the 3 locked dims per package from `<package>-<dim>.csv` (columns
    exactly key,key_display_name,count). No cross-tab is invented — `dimension`
    stays a column (2026-09-26 aggregation-snapshot contract). A missing
    dimension file skips that dimension's rows (edge rule); rows are read
    dtype=str so keys (years, institution URLs) stay verbatim strings and
    quoted commas round-trip without column shift.
    """
    rows: list[tuple[str, str, str, str, int]] = []
    for package in packages:
        for dim in OPENALEX_DIMS:
            path = Path(snapshot_dir) / f"{package}-{dim}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path, dtype=str, keep_default_na=False)
            for rec in df.itertuples(index=False):
                rows.append((package, dim, rec.key, rec.key_display_name, int(rec.count)))
    out = pd.DataFrame(rows, columns=["package", "dimension", "key", "key_display_name", "count"])
    return _sorted_by_package(out, packages, ["package", "dimension", "key"])


def _ctgov_study_fields(study: dict) -> tuple[str, str, list[str]]:
    """(overall_status, phase, distinct countries) for one nested study record.

    Edge rules (spec § Derivation edge rules): phase = designModule.phases[0]
    when present and non-empty, else literal "NA" (observational studies lack
    the field — the bucket surfaces them honestly, rows are never dropped);
    multi-phase studies attribute to the FIRST phase only (exploding would
    double-count studies); status falls back to "NA" when absent so the bucket
    always has a row. Countries are DISTINCT per study (3 US sites count the
    United States once). Location paths: the committed snapshots carry
    locations at protocolSection.contactsLocationsModule.locations[].country
    (CT.gov API v2 JSON); the spec's evidence text names
    protocolSection.locationList[].geoLoc.country (API v1 shape). Both are
    read — disk is the source of truth (decisions.md 2026-09-28), and the
    spec's AC8 behavior (country counts) must not silently vanish. See
    specs/issue-9-progress.md ## Errors.
    """
    proto = study.get("protocolSection") or {}
    status = (proto.get("statusModule") or {}).get("overallStatus") or "NA"
    phases = (proto.get("designModule") or {}).get("phases") or []
    phase = str(phases[0]) if phases else "NA"
    countries: set[str] = set()
    for loc in (proto.get("contactsLocationsModule") or {}).get("locations") or []:
        country = str(loc.get("country") or "").strip()
        if country:
            countries.add(country)
    for loc in proto.get("locationList") or []:
        country = str((loc.get("geoLoc") or {}).get("country") or "").strip()
        if country:
            countries.add(country)
    return str(status), phase, sorted(countries)


def derive_ctgov_counts(snapshot_dir: Path, packages: list[str]) -> pd.DataFrame:
    """Flatten nested study JSONL to per-package counts — ONLY status/phase/country (AC8).

    One stdlib json.loads per line (side-steps pandas read_json dtype
    inference, extdocs); a missing <package>.jsonl yields no rows (0 studies,
    surfaced via coverage flags — rows are never dropped). No PII / full study
    records reach processed output.
    """
    rows: list[tuple[str, str, str, int]] = []
    for package in packages:
        path = Path(snapshot_dir) / f"{package}.jsonl"
        if not path.exists():
            continue
        status_c: Counter[str] = Counter()
        phase_c: Counter[str] = Counter()
        country_c: Counter[str] = Counter()
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue  # blank separator lines are not studies
                status, phase, countries = _ctgov_study_fields(json.loads(line))
                status_c[status] += 1
                phase_c[phase] += 1
                country_c.update(countries)
        for dim, counter in (("overall_status", status_c), ("phase", phase_c), ("country", country_c)):
            for key, n in counter.items():
                rows.append((package, dim, key, n))
    out = pd.DataFrame(rows, columns=["package", "dimension", "key", "count"])
    return _sorted_by_package(out, packages, ["package", "dimension", "key"])


def normalize_eur(value: str) -> float:
    """CORDIS European comma-decimal EUR string -> float (AC7), BEFORE summing.

    "" -> 0.0 (blank cells never poison float()); "6995052,5" -> 6995052.5;
    bare integers pass through. More than one comma -> ValueError: the
    committed data uses the comma as decimal separator only (no thousands
    separators observed), so a multi-comma value is ambiguous and we never
    guess. pandas' decimal="," param exists but cannot raise on that case,
    hence the hand-rolled helper (extdocs).
    """
    text = (value or "").strip()
    if not text:
        return 0.0
    if text.count(",") > 1:
        raise ValueError(
            f"ambiguous EUR value {value!r}: more than one comma "
            "(decimal separator or thousands separator? never guess)"
        )
    return float(text.replace(",", "."))


def _read_cordis_dataset(snapshot_dir: Path, package: str, dataset: str) -> pd.DataFrame | None:
    """One normalized CORDIS snapshot (locked 8-col schema) or None if absent.

    dtype=str + keep_default_na=False per the 2026-09-28 CORDIS contract:
    values stay strings, empty cells stay "" (feeds normalize_eur), and a
    header-only (zero-match) file reads back as an empty frame — 0 projects,
    the package still appears elsewhere, rows are never dropped.
    """
    path = Path(snapshot_dir) / f"{package}-{dataset}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def derive_cordis_funding(snapshot_dir: Path, packages: list[str]) -> pd.DataFrame:
    """Funding totals per package × programme (FR5): h2020 + he pooled.

    programme is taken VERBATIM from the column (not the file stem). Sums are
    accumulated in fixed file order (package, dataset, row) so float totals
    are reproducible; rounded to 2 dp here, formatted to fixed 2-decimal
    strings at write time (Risk 3 mitigation, sub-task 5).
    """
    projects: dict[str, Counter[str]] = {}  # package -> {programme -> n}
    totals: dict[str, dict[str, float]] = {}  # package -> {programme -> eur}
    for package in packages:
        proj_c: Counter[str] = Counter()
        eur_t: dict[str, float] = {}
        for dataset in CORDIS_DATASETS:
            df = _read_cordis_dataset(snapshot_dir, package, dataset)
            if df is None or df.empty:
                continue
            for rec in df.itertuples(index=False):
                prog = str(rec.programme)
                proj_c[prog] += 1
                eur_t[prog] = eur_t.get(prog, 0.0) + normalize_eur(rec.eu_contribution)
        projects[package] = proj_c
        totals[package] = eur_t
    rows = [
        (package, prog, projects[package][prog], round(totals[package][prog], 2))
        for package in packages
        for prog in sorted(projects[package])
    ]
    out = pd.DataFrame(rows, columns=["package", "programme", "projects", "eu_contribution_eur"])
    return _sorted_by_package(out, packages, ["package", "programme"])


def derive_cordis_status(snapshot_dir: Path, packages: list[str]) -> pd.DataFrame:
    """Project counts per package × status, both datasets pooled (FR5)."""
    rows: list[tuple[str, str, int]] = []
    for package in packages:
        status_c: Counter[str] = Counter()
        for dataset in CORDIS_DATASETS:
            df = _read_cordis_dataset(snapshot_dir, package, dataset)
            if df is None or df.empty:
                continue
            for rec in df.itertuples(index=False):
                status_c[str(rec.status)] += 1
        for status, n in sorted(status_c.items()):
            rows.append((package, status, n))
    out = pd.DataFrame(rows, columns=["package", "status", "projects"])
    return _sorted_by_package(out, packages, ["package", "status"])


def _totals_by_package(df: pd.DataFrame, value: str,
                       dim_col: str | None = None, dim_val: str | None = None) -> dict[str, int]:
    """Sum `value` per package WITHOUT groupby.

    pandas 3.0 flipped groupby's `observed` default to True — grouping the
    categorical `package` column would silently drop zero-row packages
    (extdocs). Explicit accumulation + .get(pkg, 0) downstream guarantees
    AC4's "missing cells appear as 0; rows are never dropped".
    """
    sub = df if dim_col is None else df[df[dim_col] == dim_val]
    totals: dict[str, int] = {}
    for rec in sub.itertuples(index=False):
        pkg = str(rec.package)
        totals[pkg] = totals.get(pkg, 0) + int(getattr(rec, value))
    return totals


def build_overview(packages: list[str], landscape: pd.DataFrame,
                   ctgov: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """One row per package in config order (AC4/FR6); missing cells = 0.

    openalex_works sums the publication_year marginal ONLY (countries/
    institutions marginals double-count multi-author works — 2026-09-26
    contract); ctgov_studies sums the overall_status bucket (exactly one
    status per study == JSONL line count); cordis figures pool both
    datasets via the funding table.
    """
    works = _totals_by_package(landscape, "count", "dimension", "publication_year")
    studies = _totals_by_package(ctgov, "count", "dimension", "overall_status")
    projects = _totals_by_package(funding, "projects")
    eur: dict[str, float] = {}
    for rec in funding.itertuples(index=False):
        pkg = str(rec.package)
        eur[pkg] = eur.get(pkg, 0.0) + float(rec.eu_contribution_eur)
    rows = [
        {
            "package": pkg,
            "openalex_works": works.get(pkg, 0),
            "ctgov_studies": studies.get(pkg, 0),
            "cordis_projects": projects.get(pkg, 0),
            "cordis_eu_contribution_eur": round(eur.get(pkg, 0.0), 2),
        }
        for pkg in packages
    ]
    out = pd.DataFrame(
        rows,
        columns=["package", "openalex_works", "ctgov_studies", "cordis_projects",
                 "cordis_eu_contribution_eur"],
    )
    return _sorted_by_package(out, packages, ["package"])


def build_coverage(packages: list[str], snapshot_dates: dict[str, str],
                   record_counts: dict[tuple[str, str], int],
                   roles: dict[tuple[str, str], str]) -> pd.DataFrame:
    """package × source grid, 18 rows for 6×3 — never dropped (AC5/FR6, v2 roles).

    Rows are built explicitly from config × SOURCES (never a groupby —
    pandas 3.0 observed=True would drop empty cells). The `role` column is
    resolved from config/evidence_map.yaml (driver/context/n/a, issue-53
    D-03); the retired full/thin/empty completeness labels must not appear
    anywhere in the output. An n/a cell (documented, not pulled) carries
    record_count 0 honestly. snapshot_date is the resolved date-dir name
    already on disk (FR9: no new timestamps).
    """
    rows = []
    for pkg in packages:
        for source in SOURCES:
            n = record_counts.get((pkg, source), 0)
            role = roles.get((pkg, source))
            if role is None:
                raise ValueError(
                    f"{pkg} × {source} has no declared role — every evidence "
                    f"count carries a role (D-03, AC-4)"
                )
            rows.append({
                "package": pkg,
                "source": source,
                "snapshot_date": snapshot_dates[source],
                "record_count": n,
                "role": role,
            })
    out = pd.DataFrame(
        rows,
        columns=["package", "source", "snapshot_date", "record_count", "role"],
    )
    # BOTH orderings locked: package = config order, source = SOURCES order —
    # plain string sort would silently alphabetize them (AC3/AC5).
    out["package"] = pd.Categorical(out["package"], categories=packages, ordered=True)
    out["source"] = pd.Categorical(out["source"], categories=list(SOURCES), ordered=True)
    return out.sort_values(["package", "source"], kind="mergesort").reset_index(drop=True)


def main(*, base_dir: Path | str | None = None, out_dir: Path | str | None = None,
         config_path: Path | str | None = None,
         evidence_map_path: Path | str | None = None) -> int:
    """Build all six processed tables (AC1/AC13). Returns 0.

    Keyword-only seams mirror pull_ctgov.main / _common._resolve_base so gate
    scripts run this in-process over fixtures in a tmp dir; defaults are
    REPO_ROOT-anchored, so the script works from any cwd. A source with no
    snapshot dir raises FileNotFoundError naming the source BEFORE anything
    is written — no partial output (AC9, gate G-9). A missing evidence role
    for any config package × source raises ValueError just as loudly (D-03).
    """
    base = _resolve(base_dir, RAW_DIR)
    out = _resolve(out_dir, PROCESSED_DIR)
    packages = load_packages(_resolve(config_path, PACKAGES_PATH))
    roles = load_evidence_roles(_resolve(evidence_map_path, EVIDENCE_MAP_PATH), packages)

    # Resolve every source first: loud failure with zero writes (AC9).
    dirs = {source: latest_snapshot_dir(base, source) for source in SOURCES}

    landscape = derive_openalex_landscape(dirs["openalex"], packages)
    ctgov = derive_ctgov_counts(dirs["ctgov"], packages)
    funding = derive_cordis_funding(dirs["cordis"], packages)
    status = derive_cordis_status(dirs["cordis"], packages)
    overview = build_overview(packages, landscape, ctgov, funding)

    record_counts: dict[tuple[str, str], int] = {}
    oa = _totals_by_package(landscape, "count", "dimension", "publication_year")
    cg = _totals_by_package(ctgov, "count", "dimension", "overall_status")
    co = _totals_by_package(funding, "projects")
    for pkg in packages:
        record_counts[(pkg, "openalex")] = oa.get(pkg, 0)
        record_counts[(pkg, "ctgov")] = cg.get(pkg, 0)
        record_counts[(pkg, "cordis")] = co.get(pkg, 0)
    coverage = build_coverage(packages, {s: dirs[s].name for s in SOURCES}, record_counts, roles)

    # EUR columns at fixed 2-decimal format at write time (Risk 3 mitigation):
    # never 1e+06 / rounding drift, byte-stable across runs.
    funding = funding.assign(
        eu_contribution_eur=funding["eu_contribution_eur"].map(lambda v: f"{v:.2f}")
    )
    overview = overview.assign(
        cordis_eu_contribution_eur=overview["cordis_eu_contribution_eur"].map(lambda v: f"{v:.2f}")
    )

    tables = {
        "openalex_landscape": landscape,
        "ctgov_counts": ctgov,
        "cordis_funding": funding,
        "cordis_status": status,
        "overview": overview,
        "coverage": coverage,
    }
    for stem, df in tables.items():
        path = write_table(df, out, stem)
        print(f"wrote {path} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
