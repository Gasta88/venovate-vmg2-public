"""Ad-hoc verification gates for scripts/build_tables.py (issue-9).

The repo has no test runner, so this committed
gate script substitutes for one. Run:

    uv run python scripts/check_tables.py

Prints `G-<n> PASS|FAIL` per gate (G-1…G-9 + G-INT integration over the REAL
committed snapshots) and exits non-zero if any gate fails. Zero network
access; every write goes into per-gate tempfile.TemporaryDirectory dirs —
G-INT writes only to its tmp dir and asserts the real data/processed/ tree
is untouched. Mirrors check_common.py's runner and fixture style.

Fixture packages are `zeta_pkg`/`alpha_pkg` — config order deliberately
DIFFERS from alphabetical so any accidental lexical sort fails the gates.
The five real package names appear nowhere in this file's fixtures; only
G-INT references committed-data names, read through the real config.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_tables as bt  # noqa: E402

FIXTURE_PACKAGES = ("zeta_pkg", "alpha_pkg")  # config order != alphabetical
FIXTURE_DATE = "2026-09-28"
STEMS = ("openalex_landscape", "ctgov_counts", "cordis_funding", "cordis_status",
         "overview", "coverage")

CORDIS_HEADER = "title,objective,eu_contribution,project_id,status,start_date,end_date,programme\n"

# Fixture evidence_map (issue-53 D-03): zeta pulls on all three sources
# (openalex feeds a sizing driver), alpha's ctgov cell is n/a — documented,
# not pulled (the fixture tree has no alpha ctgov file, mirroring the real
# p2_insilico_workshop x ctgov omission).
EVIDENCE_MAP_YAML = """\
- {package: zeta_pkg, source: openalex, dimension: publication_year, role: driver,
   feeds: zeta.tam.active_labs}
- {package: zeta_pkg, source: ctgov, role: context}
- {package: zeta_pkg, source: cordis, role: context}
- {package: alpha_pkg, source: openalex, role: context}
- {package: alpha_pkg, source: ctgov, role: n/a, basis: "fixture: documented, not pulled"}
- {package: alpha_pkg, source: cordis, role: context}
"""


def write_evidence_map(tmp: Path) -> Path:
    cfg = tmp / "evidence_map.yaml"
    cfg.write_text(EVIDENCE_MAP_YAML, encoding="utf-8")
    return cfg


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


def _study(nct_id: str, status: str, design_module: dict | None,
           countries: list[str] | None, spec_shape_locations: list[str] | None = None) -> str:
    """One nested CT.gov study record (API v2 shape; spec_shape uses locationList)."""
    proto: dict = {
        "identificationModule": {"nctId": nct_id},
        "statusModule": {"overallStatus": status},
    }
    if design_module is not None:
        proto["designModule"] = design_module
    if countries is not None:
        proto["contactsLocationsModule"] = {
            "locations": [{"country": c} for c in countries]
        }
    if spec_shape_locations is not None:
        proto["locationList"] = [{"geoLoc": {"country": c}} for c in spec_shape_locations]
    return json.dumps({"protocolSection": proto})


def write_fixture_tree(tmp: Path) -> Path:
    """data/raw-shaped tree covering every derivation edge rule."""
    base = tmp / "raw"

    oa = base / "openalex" / FIXTURE_DATE
    oa.mkdir(parents=True)
    (base / "openalex" / "README.md").write_text("not a date dir\n", encoding="utf-8")
    (base / "openalex" / ".gitkeep").write_text("", encoding="utf-8")
    # zeta: all 3 dims. countries marginal (70) deliberately != works (57):
    # summing anything but publication_year into totals must fail the gates.
    (oa / "zeta_pkg-publication_year.csv").write_text(
        "key,key_display_name,count\n2015,2015,27\n2016,2016,30\n", encoding="utf-8")
    (oa / "zeta_pkg-authorships.countries.csv").write_text(
        "key,key_display_name,count\nUS,United States,40\nDE,Germany,30\n", encoding="utf-8")
    (oa / "zeta_pkg-authorships.institutions.id.csv").write_text(
        'key,key_display_name,count\n'
        '"https://openalex.org/I1","Univ, With Commas",5\n'
        "https://openalex.org/I2,Plain Univ,2\n", encoding="utf-8")
    # alpha: publication_year only — missing dimension files are skipped.
    (oa / "alpha_pkg-publication_year.csv").write_text(
        "key,key_display_name,count\n2020,2020,7\n", encoding="utf-8")

    ct = base / "ctgov" / FIXTURE_DATE
    ct.mkdir(parents=True)
    studies = [
        # (a) interventional, multi-phase, 2 same-country sites + 1 other
        _study("NCT1", "COMPLETED", {"studyType": "INTERVENTIONAL",
                                     "phases": ["PHASE1", "PHASE2"]},
               ["United States", "United States", "Germany"]),
        # (b) observational, NO designModule.phases field at all
        _study("NCT2", "RECRUITING", {"studyType": "OBSERVATIONAL"}, ["United States"]),
        # (c) empty phases list + no locations module (country asymmetry)
        _study("NCT3", "COMPLETED", {"studyType": "INTERVENTIONAL", "phases": []}, None),
        # (d) spec-literal locationList[].geoLoc.country shape, duplicate site
        _study("NCT4", "ACTIVE_NOT_RECRUITING", {"studyType": "INTERVENTIONAL",
                                                 "phases": ["EARLY_PHASE1"]},
               None, spec_shape_locations=["France", "France"]),
    ]
    (ct / "zeta_pkg.jsonl").write_text("\n".join(studies) + "\n", encoding="utf-8")
    # alpha_pkg.jsonl absent entirely -> 0 studies, never a crash.

    co = base / "cordis" / FIXTURE_DATE
    co.mkdir(parents=True)
    (co / "zeta_pkg-h2020.csv").write_text(CORDIS_HEADER + (
        'T1,obj,"6995052,5",1,CLOSED,2015-06-01,2019-05-31,H2020\n'
        "T2,obj,8056677,2,CLOSED,2016-01-01,2020-12-31,H2020\n"
        'T3,obj,"",3,TERMINATED,2014-01-01,2015-12-31,H2020\n'
        # HORIZON row inside the h2020 FILE: programme groups by COLUMN, not stem.
        'T4,obj,"1250000,25",4,SIGNED,2021-01-01,2024-12-31,HORIZON\n'
    ), encoding="utf-8")
    (co / "zeta_pkg-he.csv").write_text(CORDIS_HEADER, encoding="utf-8")  # zero-match
    # alpha_pkg cordis files absent entirely -> 0 projects, never a crash.
    return base


def fixture(tmp: Path) -> tuple[Path, Path, list[str]]:
    cfg = write_packages_yaml(tmp)
    base = write_fixture_tree(tmp)
    return base, cfg, list(FIXTURE_PACKAGES)


def _fixture_roles(tmp: Path) -> dict[tuple[str, str], str]:
    return bt.load_evidence_roles(write_evidence_map(tmp), ["zeta_pkg", "alpha_pkg"])


def _sha_map(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def _cell(df: pd.DataFrame, **filt) -> int:
    sub = df.copy()
    for col, val in filt.items():
        sub = sub[sub[col] == val]
    assert len(sub) == 1, (filt, len(sub))
    return int(sub["count" if "count" in sub.columns else "projects"].iloc[0])


# --- unit gates: one per derivation/assembly function ---------------------------------


def g1_latest_dir(tmp: Path) -> None:
    base = tmp / "raw"
    (base / "openalex" / "2026-01-01").mkdir(parents=True)
    (base / "openalex" / FIXTURE_DATE).mkdir()
    (base / "openalex" / "README.md").write_text("ignore me\n", encoding="utf-8")
    (base / "openalex" / ".gitkeep").write_text("", encoding="utf-8")
    assert bt.latest_snapshot_dir(base, "openalex").name == FIXTURE_DATE
    (base / "dateless").mkdir()
    (base / "dateless" / "notes.txt").write_text("x", encoding="utf-8")
    try:
        bt.latest_snapshot_dir(base, "dateless")
    except FileNotFoundError as exc:
        assert "dateless" in str(exc), exc
    else:
        raise AssertionError("date-dir-less source must raise FileNotFoundError")
    try:
        bt.latest_snapshot_dir(base, "never_pulled")
    except FileNotFoundError as exc:
        assert "never_pulled" in str(exc), exc
    else:
        raise AssertionError("absent source dir must raise FileNotFoundError naming source")
    # packages.yaml helper: ordered names, ValueError on empty.
    cfg = write_packages_yaml(tmp)
    assert bt.load_packages(cfg) == list(FIXTURE_PACKAGES)
    (tmp / "empty.yaml").write_text("segments: []\n", encoding="utf-8")
    try:
        bt.load_packages(tmp / "empty.yaml")
    except ValueError:
        pass
    else:
        raise AssertionError("package-less config must raise ValueError")


def g2_openalex(tmp: Path) -> None:
    base, _cfg, pkgs = fixture(tmp)
    df = bt.derive_openalex_landscape(base / "openalex" / FIXTURE_DATE, pkgs)
    assert list(df.columns) == ["package", "dimension", "key", "key_display_name", "count"]
    assert len(df) == 7  # 2+2+2 (zeta) + 1 (alpha); row count == sum of fixture rows
    assert list(df["package"].drop_duplicates()) == list(FIXTURE_PACKAGES)  # config order
    assert set(df["dimension"]) == set(bt.OPENALEX_DIMS)  # dimension stays a COLUMN
    # no cross-tab invented: each dim's keys are exactly its file's keys
    for dim, keys in (("publication_year", {"2015", "2016"}),
                      ("authorships.countries", {"US", "DE"}),
                      ("authorships.institutions.id",
                       {"https://openalex.org/I1", "https://openalex.org/I2"})):
        got = set(df[(df["dimension"] == dim) & (df["package"] == "zeta_pkg")]["key"])
        assert got == keys, (dim, got)
    # quoted comma-in-name round-trips without column shift
    inst = df[(df["dimension"] == "authorships.institutions.id") & (df["key"] == "https://openalex.org/I1")]
    assert inst["key_display_name"].iloc[0] == "Univ, With Commas"
    # works total derives from publication_year ONLY: 57, never countries' 70
    works = bt._totals_by_package(df, "count", "dimension", "publication_year")
    assert works == {"zeta_pkg": 57, "alpha_pkg": 7}
    # keys sorted ascending as strings within package × dim
    z = df[(df["package"] == "zeta_pkg") & (df["dimension"] == "publication_year")]
    assert list(z["key"]) == sorted(z["key"])
    # missing dimension file (alpha countries/institutions) -> skipped, no crash
    assert len(df[df["package"] == "alpha_pkg"]) == 1


def g3_ctgov(tmp: Path) -> None:
    base, _cfg, pkgs = fixture(tmp)
    df = bt.derive_ctgov_counts(base / "ctgov" / FIXTURE_DATE, pkgs)
    assert list(df.columns) == ["package", "dimension", "key", "count"]
    assert set(df["dimension"]) == {"overall_status", "phase", "country"}
    assert list(df["package"].drop_duplicates()) == ["zeta_pkg"]  # alpha: no file -> no rows
    # status: one row per study; phase: first phase only (PHASE1, not PHASE2)
    assert _cell(df, package="zeta_pkg", dimension="overall_status", key="COMPLETED") == 2
    assert _cell(df, package="zeta_pkg", dimension="overall_status", key="RECRUITING") == 1
    assert _cell(df, package="zeta_pkg", dimension="overall_status", key="ACTIVE_NOT_RECRUITING") == 1
    assert _cell(df, package="zeta_pkg", dimension="phase", key="PHASE1") == 1
    assert _cell(df, package="zeta_pkg", dimension="phase", key="EARLY_PHASE1") == 1
    # literal NA bucket: missing phases field AND empty list both land in NA
    assert _cell(df, package="zeta_pkg", dimension="phase", key="NA") == 2
    assert "PHASE2" not in set(df["key"])  # multi-phase NOT exploded (no double-count)
    # DISTINCT countries per study: NCT1's two US sites count United States once
    assert _cell(df, package="zeta_pkg", dimension="country", key="United States") == 2
    assert _cell(df, package="zeta_pkg", dimension="country", key="Germany") == 1
    # spec-literal locationList[].geoLoc.country shape also yields counts, deduped
    assert _cell(df, package="zeta_pkg", dimension="country", key="France") == 1
    # status/phase always have a row; country is the documented asymmetry
    assert sum(df[df["dimension"] == "overall_status"]["count"]) == 4
    assert sum(df[df["dimension"] == "phase"]["count"]) == 4
    # NCT3 (no locations module) contributes no country row
    assert sum(df[df["dimension"] == "country"]["count"]) == 4  # 3 + 1, not 5


def g4_cordis(tmp: Path) -> None:
    # normalize_eur: comma decimals, blank, bare integer, loud on ambiguity
    assert bt.normalize_eur("6995052,5") == 6995052.5
    assert bt.normalize_eur("8056677") == 8056677.0
    assert bt.normalize_eur("") == 0.0
    try:
        bt.normalize_eur("1,234,567")
    except ValueError:
        pass
    else:
        raise AssertionError("multi-comma EUR must raise ValueError (never guess)")

    base, _cfg, pkgs = fixture(tmp)
    d = base / "cordis" / FIXTURE_DATE
    f = bt.derive_cordis_funding(d, pkgs)
    assert list(f.columns) == ["package", "programme", "projects", "eu_contribution_eur"]
    # h2020: 3 H2020 rows (comma + integer + blank) and 1 HORIZON row BY COLUMN
    z_h2020 = f[(f["package"] == "zeta_pkg") & (f["programme"] == "H2020")].iloc[0]
    assert int(z_h2020["projects"]) == 3
    assert float(z_h2020["eu_contribution_eur"]) == 15051729.50  # 6995052.5+8056677+0
    z_hor = f[(f["package"] == "zeta_pkg") & (f["programme"] == "HORIZON")].iloc[0]
    assert int(z_hor["projects"]) == 1 and float(z_hor["eu_contribution_eur"]) == 1250000.25
    assert len(f) == 2  # header-only he file and absent alpha files add no rows

    s = bt.derive_cordis_status(d, pkgs)
    assert list(s.columns) == ["package", "status", "projects"]
    assert dict(zip(s["status"], s["projects"])) == {"CLOSED": 2, "SIGNED": 1, "TERMINATED": 1}
    assert int(s["projects"].sum()) == 4  # both datasets pooled


def g5_overview(tmp: Path) -> None:
    base, _cfg, pkgs = fixture(tmp)
    landscape = bt.derive_openalex_landscape(base / "openalex" / FIXTURE_DATE, pkgs)
    ctgov = bt.derive_ctgov_counts(base / "ctgov" / FIXTURE_DATE, pkgs)
    funding = bt.derive_cordis_funding(base / "cordis" / FIXTURE_DATE, pkgs)
    ov = bt.build_overview(pkgs, landscape, ctgov, funding)
    assert list(ov.columns) == ["package", "openalex_works", "ctgov_studies",
                                "cordis_projects", "cordis_eu_contribution_eur"]
    # locked config order, NOT alphabetical (alpha_pkg would sort first)
    assert list(ov["package"]) == ["zeta_pkg", "alpha_pkg"]
    z, a = (ov[ov["package"] == p].iloc[0] for p in FIXTURE_PACKAGES)
    assert (int(z["openalex_works"]), int(z["ctgov_studies"]), int(z["cordis_projects"])) == (57, 4, 4)
    assert float(z["cordis_eu_contribution_eur"]) == 16301729.75
    # package with zero ctgov rows and NO cordis files: 0 cells, row never dropped
    assert (int(a["openalex_works"]), int(a["ctgov_studies"]), int(a["cordis_projects"])) == (7, 0, 0)
    assert float(a["cordis_eu_contribution_eur"]) == 0.0


def g6_coverage(tmp: Path) -> None:
    # v2 roles (issue-53 D-03): resolved from evidence_map, never derived from
    # counts; the retired full/thin/empty labels must not appear anywhere.
    roles = _fixture_roles(tmp)
    counts = {
        ("p1", "openalex"): 0, ("p1", "ctgov"): 1, ("p1", "cordis"): 49,
        ("p2", "openalex"): 50, ("p2", "ctgov"): 120, ("p2", "cordis"): 0,
    }
    fixed_roles = {("p1", "openalex"): "n/a", ("p1", "ctgov"): "context",
                   ("p1", "cordis"): "context", ("p2", "openalex"): "driver",
                   ("p2", "ctgov"): "driver", ("p2", "cordis"): "n/a"}
    dates = {s: FIXTURE_DATE for s in bt.SOURCES}
    cov = bt.build_coverage(["p1", "p2"], dates, counts, fixed_roles)
    assert list(cov.columns) == ["package", "source", "snapshot_date", "record_count",
                                 "role"]
    got = {(r.package, r.source): (int(r.record_count), r.role)
           for r in cov.itertuples(index=False)}
    for key, role in fixed_roles.items():
        assert got[key][1] == role, (key, got[key])  # role passes through verbatim
    assert got[("p1", "openalex")] == (0, "n/a")  # n/a: count 0, documented, not padded
    assert got[("p2", "ctgov")] == (120, "driver")  # count never re-derives the role
    assert (cov["snapshot_date"] == FIXTURE_DATE).all()  # date-dir name, no new timestamps
    # 18-row invariant: 6 packages × 3 sources, rows never dropped (even all-empty)
    six = [f"pkg_{i}" for i in range(6)]
    six_roles = {(p, s): "context" for p in six for s in bt.SOURCES}
    cov18 = bt.build_coverage(six, dates, {}, six_roles)
    assert len(cov18) == 18 and set(cov18["role"]) == {"context"}
    # locked orders: package = config order (not alphabetical), source = SOURCES order
    assert list(cov18["package"]) == [p for p in six for _ in bt.SOURCES]
    assert list(cov18["source"]) == list(bt.SOURCES) * 6
    # missing role for any package x source fails loudly (D-03/AC-4 precondition)
    incomplete = {k: v for k, v in fixed_roles.items() if k != ("p2", "cordis")}
    try:
        bt.build_coverage(["p1", "p2"], dates, counts, incomplete)
    except ValueError as exc:
        assert "no declared role" in str(exc), exc
    else:
        raise AssertionError("package x source without a role must raise ValueError")
    # load_evidence_roles: most load-bearing role wins; unknown role/package/source
    # and missing cells raise naming the offender.
    em = write_evidence_map(tmp)
    resolved = bt.load_evidence_roles(em, ["zeta_pkg", "alpha_pkg"])
    assert resolved[("zeta_pkg", "openalex")] == "driver"
    assert resolved[("alpha_pkg", "ctgov")] == "n/a"
    bad_role = tmp / "bad_role.yaml"
    bad_role.write_text("- {package: zeta_pkg, source: openalex, role: full}\n"
                        "- {package: zeta_pkg, source: ctgov, role: context}\n"
                        "- {package: zeta_pkg, source: cordis, role: context}\n"
                        "- {package: alpha_pkg, source: openalex, role: context}\n"
                        "- {package: alpha_pkg, source: ctgov, role: n/a}\n"
                        "- {package: alpha_pkg, source: cordis, role: context}\n",
                        encoding="utf-8")
    try:
        bt.load_evidence_roles(bad_role, ["zeta_pkg", "alpha_pkg"])
    except ValueError as exc:
        assert "role" in str(exc), exc
    else:
        raise AssertionError("retired completeness label as role must raise")
    ghost_map = tmp / "ghost_map.yaml"
    ghost_map.write_text("- {package: ghost_pkg, source: openalex, role: driver}\n",
                         encoding="utf-8")
    try:
        bt.load_evidence_roles(ghost_map, ["zeta_pkg"])
    except ValueError as exc:
        assert "ghost_pkg" in str(exc), exc
    else:
        raise AssertionError("unknown package in evidence_map must raise")
    # zeta x ctgov missing from a partial map -> missing-cell ValueError
    partial = tmp / "partial.yaml"
    partial.write_text("- {package: zeta_pkg, source: openalex, role: driver}\n",
                       encoding="utf-8")
    try:
        bt.load_evidence_roles(partial, ["zeta_pkg"])
    except ValueError as exc:
        assert "zeta_pkg × ctgov" in str(exc), exc
    else:
        raise AssertionError("missing package x source role must raise")


def g7_determinism(tmp: Path) -> None:
    base, cfg, _pkgs = fixture(tmp)
    em = write_evidence_map(tmp)
    out_a, out_b = tmp / "a", tmp / "b"
    assert bt.main(base_dir=base, out_dir=out_a, config_path=cfg, evidence_map_path=em) == 0
    assert bt.main(base_dir=base, out_dir=out_b, config_path=cfg, evidence_map_path=em) == 0
    sha_a, sha_b = _sha_map(out_a), _sha_map(out_b)
    assert set(sha_a) == {f"{s}.csv" for s in STEMS} == set(sha_b)
    assert sha_a == sha_b, "two runs into separate dirs must be byte-identical"
    # idempotent overwrite in place: no error, no new files, same bytes
    before = _sha_map(out_a)
    assert bt.main(base_dir=base, out_dir=out_a, config_path=cfg, evidence_map_path=em) == 0
    assert _sha_map(out_a) == before
    # EUR columns written at fixed 2-decimal format (no 1e+06 / drift)
    funding = (out_a / "cordis_funding.csv").read_text("utf-8")
    assert "15051729.50" in funding and "1250000.25" in funding, funding
    assert "1e" not in funding.lower(), funding


def g8_end_to_end(tmp: Path) -> None:
    base, cfg, _pkgs = fixture(tmp)
    em = write_evidence_map(tmp)
    out = tmp / "out"
    assert bt.main(base_dir=base, out_dir=out, config_path=cfg, evidence_map_path=em) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(f"{s}.csv" for s in STEMS), names  # exactly 6, no manifests
    assert not list(out.glob("*.manifest.json")) and not list(out.glob("*.json"))
    for stem in STEMS:
        df = pd.read_csv(out / f"{stem}.csv", dtype=str)
        assert set(df["package"]) <= set(FIXTURE_PACKAGES), stem
    # keep_default_na=False: pandas' default na_values include "n/a" — the
    # role cell would silently become NaN without it (pandas 3.0 pattern).
    cov = pd.read_csv(out / "coverage.csv", dtype=str, keep_default_na=False)
    assert len(cov) == 6  # 2 fixture packages × 3 sources
    assert list(cov.columns) == ["package", "source", "snapshot_date", "record_count", "role"]
    # retired completeness labels must not appear as roles anywhere (D-03)
    roles = set(cov["role"])
    assert roles <= {"driver", "context", "n/a"}, roles
    ov_txt = (out / "overview.csv").read_text("utf-8")
    assert "16301729.75" in ov_txt and "0.00" in ov_txt, ov_txt  # missing cells = 0
    # no PII / full study records anywhere in processed output
    blob = "".join(p.read_text("utf-8") for p in out.glob("*.csv"))
    assert "NCT" not in blob and "briefTitle" not in blob


def g9_loud_fail(tmp: Path) -> None:
    base, cfg, _pkgs = fixture(tmp)
    em = write_evidence_map(tmp)
    shutil.rmtree(base / "cordis")
    out = tmp / "out"
    try:
        bt.main(base_dir=base, out_dir=out, config_path=cfg, evidence_map_path=em)
    except FileNotFoundError as exc:
        assert "cordis" in str(exc), exc
    else:
        raise AssertionError("missing source must abort main() with FileNotFoundError")
    # derive-all-then-write-all: no partial output for the failed run
    assert not out.exists() or not list(out.glob("*.csv")), list(out.iterdir())


def g_int_real_data(tmp: Path) -> None:
    """Offline smoke over the REAL committed snapshots; real data/processed untouched."""
    processed = REPO_ROOT / "data" / "processed"
    raw_before = _sha_map(processed) if processed.is_dir() else {}
    out = tmp / "real_out"
    assert bt.main(out_dir=out) == 0  # defaults: repo data/raw + real config/evidence_map
    raw_after = _sha_map(processed) if processed.is_dir() else {}
    assert raw_before == raw_after, "G-INT must not touch the real data/processed/ tree"

    pkgs = bt.load_packages()
    assert len(pkgs) == 6
    ov = pd.read_csv(out / "overview.csv", dtype={"package": str})
    assert list(ov["package"]) == pkgs  # config order end to end
    ven = ov[ov["package"] == "p5_venomate"].iloc[0]  # committed-data baseline
    # (retuned queries, latest committed snapshot: venom-peptide corpus 412 works,
    # 35 envenomation studies, 212 venom/toxin-keyword CORDIS projects — counts
    # identical between the 2026-10-05 and 2026-10-06 pulls)
    assert (int(ven["openalex_works"]), int(ven["ctgov_studies"]), int(ven["cordis_projects"])) \
        == (412, 35, 212), dict(ven)
    # EUR written at fixed 2-decimal format in the raw bytes (Risk 3 guard)
    ven_line = next(l for l in (out / "overview.csv").read_text("utf-8").splitlines()
                    if l.startswith("p5_venomate,"))
    assert re.fullmatch(r"p5_venomate,\d+,\d+,\d+,\d+\.\d{2}", ven_line), ven_line

    # keep_default_na=False: pandas' default na_values include "n/a" (the role
    # cell must stay the verbatim string, pandas 3.0 pattern).
    cov = pd.read_csv(out / "coverage.csv", dtype={"package": str, "snapshot_date": str},
                      keep_default_na=False)
    # Snapshot-date needle follows the latest committed pull (append-only raw
    # layer: the 2026-10-06 re-pull carries the identical counts, only the
    # dated dir moved — the gate asserts the date the pipeline must produce).
    assert len(cov) == 18 and (cov["snapshot_date"] == "2026-10-06").all()
    assert list(cov.columns) == ["package", "source", "snapshot_date", "record_count", "role"]
    roles = {(r.package, r.source): r.role for r in cov.itertuples(index=False)}
    # retired completeness labels are gone from the real artifact (D-03)
    assert set(roles.values()) <= {"driver", "context", "n/a"}, set(roles.values())
    assert set(roles.values()) & {"full", "thin", "empty"} == set()
    # every package's openalex count is its sizing driver anchor (evidence_map)
    for pkg in pkgs:
        assert roles[(pkg, "openalex")] == "driver", pkg
    # the documented n/a cell: p2_insilico_workshop x ctgov, 0 records, not pulled
    assert roles[("p2_insilico_workshop", "ctgov")] == "n/a"
    counts = {(r.package, r.source): int(r.record_count) for r in cov.itertuples(index=False)}
    assert counts[("p2_insilico_workshop", "ctgov")] == 0
    # every other cell carries real pulled counts (p6_gem included — D-02
    # widened the universe; never a padded zero row)
    for (pkg, src), n in counts.items():
        if (pkg, src) == ("p2_insilico_workshop", "ctgov"):
            continue
        assert n > 0, (pkg, src)
    # coverage counts agree with overview totals (same derivation, two tables)
    srcmap = {"openalex": "openalex_works", "ctgov": "ctgov_studies",
              "cordis": "cordis_projects"}
    for rec in ov.itertuples(index=False):
        pkg = str(rec.package)
        for src, col in srcmap.items():
            assert counts[(pkg, src)] == int(getattr(rec, col)), (pkg, src)
    # every output table carries the package join key in locked order (AC3)
    for stem in STEMS:
        df = pd.read_csv(out / f"{stem}.csv", dtype={"package": str})
        assert list(df["package"].drop_duplicates()) == \
            [p for p in pkgs if p in set(df["package"])], stem


def g0_no_common_import(tmp: Path) -> None:
    """AC11/DoD-6: build_tables.py must NOT import or call _common.write_snapshot."""
    script = REPO_ROOT / "scripts" / "build_tables.py"
    content = script.read_text(encoding="utf-8")
    assert "import _common" not in content and "from _common" not in content, \
        "build_tables.py must NOT import _common (AC11)"
    assert "write_snapshot" not in content or "NOT reused" in content, \
        "build_tables.py must NOT call write_snapshot (AC11); only 'NOT reused' comment allowed"


def main() -> int:
    failures = 0
    for gate_id, fn in (
        ("G-0", g0_no_common_import),
        ("G-1", g1_latest_dir),
        ("G-2", g2_openalex),
        ("G-3", g3_ctgov),
        ("G-4", g4_cordis),
        ("G-5", g5_overview),
        ("G-6", g6_coverage),
        ("G-7", g7_determinism),
        ("G-8", g8_end_to_end),
        ("G-9", g9_loud_fail),
        ("G-INT", g_int_real_data),
    ):
        with tempfile.TemporaryDirectory(prefix="check_tables_") as td:
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
