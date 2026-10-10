"""make_numbers: data/processed/market_sizing.csv -> report/generated/numbers.yaml.

Generated numbers artifact (issue-53 D-11/WI-07 infra): every report number,
keyed `pX.layer.quantile` — the keys mirror the prose placeholders exactly
(`{{p5.tam.p90}}`, `{{p4.som.p50}}`). The pandoc Lua filter
`report/filters/numbers.lua` substitutes these into the document so no
monetary figure is ever typed by hand (AC-7/DoD-7).

Contract:
- Input: the locked v2 market_sizing.csv schema (package,layer,p10,p50,p90,
  envelope_low,envelope_high,unit,test_in_validation) plus
  data/processed/cordis_funding.csv and data/processed/platform_revenue.csv
  (issue-57 WI-16) — all validated loudly against config/packages.yaml
  before any write (validate-all-then-write-all, size_markets precedent).
- Output: report/generated/numbers.yaml — a YAML map keyed by the package's
  P-prefix (p1_venospatial -> p1), then layer, then the five numeric cells as
  RAW INTEGERS (the artifact stores exact values; display is the renderer's
  job: the single `money_display` formatter — € + K/M/B magnitude + exactly
  one decimal — serves prose AND table cells since issue-59 WI-20 retired
  the raw-integer table rendering of Appendix B rule 4).
  Absent layers simply have no key (documented via evidence_map, never
  invented).
- CORDIS EU contributions: each package also carries a `cordis` layer read
  from data/processed/cordis_funding.csv — `{h2020: <int>, horizon: <int>}`,
  the programme's EUR contribution rounded to a raw integer — so prose
  substitutes `{{pX.cordis.h2020}}` / `{{pX.cordis.horizon}}` instead of
  typing the figures.
- Platform block (issue-57 WI-16): a top-level `platform:` map mirrors
  data/processed/platform_revenue.csv — `revenue` (the total row), `referral`
  and `profiles` × p10/p50/p90 — so the Summary substitutes
  `{{platform.revenue.p10}}` etc. The placeholder grammar widens to
  `(p[1-9]|platform)` in lockstep with report/filters/numbers.lua and
  scripts/check_numbers.py (all three mirrors, decisions.md 2026-10-06).
- Derived share of TAM (issue-57 D-23, one decimal since issue-59 WI-22):
  each package's `som` block carries `share_of_tam: <float one-decimal>` =
  round(som.p50 ÷ tam.p50 × 100, 1) — a DERIVED key
  (never a CSV cell; check_numbers G-2 exempts derived keys from the
  cell-compare and recomputes the formula instead, tolerance 0.05).
  `{{pX.som.share_of_tam}}` is table-cell-only: the Lua prose renderer would
  money-format it as €. A package without a `tam` or `som` row fails loudly
  (never a silent skip or a divide-by-zero).
- Determinism: fixed key order (config package order x LAYERS order,
  platform block last), hand-
  emitted lines (no yaml.dump float formatting drift), utf-8, "\\n" endings,
  single trailing newline, plain overwrite — re-runs are byte-identical
  no-ops. _common.write_snapshot is NOT reused (dated dirs/manifests fit raw
  pulls, not stable committed artifacts). Fully offline: no network, no
  timestamps.

Run: uv run python scripts/make_numbers.py   (or: make numbers)
Gates: uv run python scripts/check_numbers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
OUT_PATH = REPO_ROOT / "report" / "generated" / "numbers.yaml"
PACKAGES_PATH = REPO_ROOT / "config" / "packages.yaml"

# Locked v2 input schema (mirrors size_markets.OUT_COLUMNS — the same
# self-contained duplication ruling as make_figures vs size_markets).
MARKET_SIZING_HEADER = ["package", "layer", "p10", "p50", "p90",
                        "envelope_low", "envelope_high", "unit", "test_in_validation"]
# Locked CORDIS funding schema (mirrors build_tables.derive_cordis_funding).
CORDIS_HEADER = ["package", "programme", "projects", "eu_contribution_eur"]
# Locked platform_revenue.csv schema (mirrors size_markets.PLATFORM_OUT_COLUMNS,
# issue-57 WI-16). The csv `total` component is emitted as the yaml `revenue`
# key — the headline the Summary substitutes with {{platform.revenue.*}}.
PLATFORM_REVENUE_HEADER = ["component", "p10", "p50", "p90"]
PLATFORM_COMPONENT_KEYS = {"profiles": "profiles", "referral": "referral",
                           "total": "revenue"}
PLATFORM_BLOCK_ORDER = ("revenue", "referral", "profiles")
# Derived som key (issue-57 D-23): round(som.p50 / tam.p50 * 100, 1) — one
# decimal since issue-59 WI-22 (P2's ceiling must render 0.1, not 0).
DERIVED_SOM_KEYS = ("share_of_tam",)
LAYERS = ("context", "tam", "som")
NUMERIC_COLS = ("p10", "p50", "p90", "envelope_low", "envelope_high")


def _resolve(path: Path | str | None, default: Path) -> Path:
    """Explicit path as given (relative -> cwd), else the repo-root default."""
    return default if path is None else Path(path)


def load_package_prefixes(config_path: Path | str = PACKAGES_PATH) -> list[tuple[str, str]]:
    """Ordered (package_name, pX prefix) pairs from config/packages.yaml.

    The prefix is the part before the first underscore and must match
    ^p[1-9]$ (D-01: keys carry Maria's numbering); anything else raises
    loudly — placeholders like {{p4.som.p50}} depend on this mapping.
    """
    import re

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    pairs: list[tuple[str, str]] = []
    for entry in data.get("packages") or []:
        name = str(entry["name"])
        prefix = name.split("_", 1)[0]
        if not re.fullmatch(r"p[1-9]", prefix):
            raise ValueError(
                f"package key {name!r} in {config_path} does not start with a "
                f"'pN_' prefix (D-01) — numbers.yaml keys would not resolve"
            )
        pairs.append((name, prefix))
    if not pairs:
        raise ValueError(f"no package names found under `packages:` in {config_path}")
    prefixes = [p for _n, p in pairs]
    if len(set(prefixes)) != len(prefixes):
        raise ValueError(f"duplicate pN prefixes in {config_path}: {prefixes}")
    return pairs


def read_market_sizing(path: Path | str, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """Read + loudly validate the v2 market_sizing.csv before any write."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != MARKET_SIZING_HEADER:
        raise ValueError(
            f"{path}: header {list(df.columns)} does not match the locked "
            f"market_sizing v2 schema {MARKET_SIZING_HEADER}"
        )
    names = [n for n, _p in pairs]
    seen: list[str] = []
    for i, rec in enumerate(df.itertuples(index=False)):
        where = f"{path} row {i + 2}"
        pkg, layer = str(rec.package), str(rec.layer)
        if pkg not in names:
            raise ValueError(f"{where}: package {pkg!r} is not in config/packages.yaml {names}")
        if layer not in LAYERS:
            raise ValueError(f"{where}: layer {layer!r} not in {list(LAYERS)}")
        for col in NUMERIC_COLS:
            text = str(getattr(rec, col)).strip()
            try:
                int(text)
            except ValueError:
                raise ValueError(f"{where}: non-integer {col} value {text!r}") from None
        if not seen or seen[-1] != pkg:
            seen.append(pkg)
    # package blocks contiguous in config order (never re-sorted); a package
    # with no rows at all is an error (every config package must be sized).
    if seen != names:
        raise ValueError(
            f"{path}: package blocks in row order {seen} do not match config order {names}"
        )
    # one row per (package, layer) at most
    pairs_seen: set[tuple[str, str]] = set()
    for rec in df.itertuples(index=False):
        key = (str(rec.package), str(rec.layer))
        if key in pairs_seen:
            raise ValueError(f"{path}: duplicate package x layer {key}")
        pairs_seen.add(key)
    return df


def read_cordis_funding(path: Path | str, pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Read data/processed/cordis_funding.csv -> {package: {programme_key: int}}.

    The programme label is lowercased (H2020 -> h2020, HORIZON -> horizon) so
    the key is a valid placeholder segment (`{{pX.cordis.h2020}}`); the EUR
    contribution is rounded to a raw integer, matching the sizing cells.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != CORDIS_HEADER:
        raise ValueError(
            f"{path}: header {list(df.columns)} does not match the locked "
            f"cordis_funding schema {CORDIS_HEADER}"
        )
    names = [n for n, _p in pairs]
    out: dict[str, dict[str, int]] = {}
    for i, rec in enumerate(df.itertuples(index=False)):
        where = f"{path} row {i + 2}"
        pkg = str(rec.package)
        if pkg not in names:
            raise ValueError(f"{where}: package {pkg!r} is not in config/packages.yaml {names}")
        key = str(rec.programme).lower()
        out.setdefault(pkg, {})[key] = int(round(float(str(rec.eu_contribution_eur))))
    return out


def read_platform_revenue(path: Path | str) -> dict[str, dict[str, int]]:
    """Read data/processed/platform_revenue.csv -> {yaml_key: {q: int}}.

    Locked issue-57 WI-16 contract: exact 4-column header, exactly the three
    components profiles/referral/total (each once), plain integer cells,
    monotonic p10 <= p50 <= p90 per row. The `total` component is exposed as
    the `revenue` yaml key (the Summary headline). Absence or drift fails
    loudly — the platform band is generated data, never a typed constant.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != PLATFORM_REVENUE_HEADER:
        raise ValueError(
            f"{path}: header {list(df.columns)} does not match the locked "
            f"platform_revenue schema {PLATFORM_REVENUE_HEADER}"
        )
    out: dict[str, dict[str, int]] = {}
    for i, rec in enumerate(df.itertuples(index=False)):
        where = f"{path} row {i + 2}"
        comp = str(rec.component)
        if comp not in PLATFORM_COMPONENT_KEYS:
            raise ValueError(f"{where}: component {comp!r} not in "
                             f"{sorted(PLATFORM_COMPONENT_KEYS)}")
        key = PLATFORM_COMPONENT_KEYS[comp]
        if key in out:
            raise ValueError(f"{where}: duplicate component {comp!r}")
        cells: dict[str, int] = {}
        for col in ("p10", "p50", "p90"):
            text = str(getattr(rec, col)).strip()
            try:
                cells[col] = int(text)
            except ValueError:
                raise ValueError(f"{where}: non-integer {col} value {text!r}") from None
        if not cells["p10"] <= cells["p50"] <= cells["p90"]:
            raise ValueError(f"{where}: quantiles not monotonic: {cells}")
        out[key] = cells
    missing = set(PLATFORM_COMPONENT_KEYS.values()) - set(out)
    if missing:
        raise ValueError(f"{path}: missing components {sorted(missing)} "
                         "(expected profiles, referral and total rows)")
    return out


def derive_share_of_tam(df: pd.DataFrame,
                       pairs: list[tuple[str, str]]) -> dict[str, float]:
    """{package_name: round(som.p50 / tam.p50 * 100, 1)} for every config package.

    Issue-57 D-23 (delivery ceiling as a share of TAM), rounded to ONE
    decimal since issue-59 WI-22 — the integer rounding rendered P2's
    positive ceiling as 0; 0.1 is the readable form. Fails loudly when a
    package lacks a som or tam row — every package carries both today; a
    future absent layer must abort the build, never divide by zero or emit a
    silently missing key (the placeholder would then leak at report time).
    """
    shares: dict[str, float] = {}
    for name, _prefix in pairs:
        sub = df[df["package"] == name]
        som = sub[sub["layer"] == "som"]
        tam = sub[sub["layer"] == "tam"]
        if som.empty or tam.empty:
            raise ValueError(
                f"{name}: share_of_tam needs both som and tam rows "
                f"(som {'present' if not som.empty else 'ABSENT'}, "
                f"tam {'present' if not tam.empty else 'ABSENT'}) — "
                f"refusing to divide by a missing layer"
            )
        som_p50 = int(str(som.iloc[0]["p50"]))
        tam_p50 = int(str(tam.iloc[0]["p50"]))
        if tam_p50 == 0:
            raise ValueError(f"{name}: tam.p50 is 0 — share_of_tam undefined")
        shares[name] = round(som_p50 / tam_p50 * 100, 1)
    return shares


def render_yaml(df: pd.DataFrame, pairs: list[tuple[str, str]],
                cordis: dict[str, dict[str, int]] | None = None,
                platform: dict[str, dict[str, int]] | None = None,
                shares: dict[str, float] | None = None) -> str:
    """Emit numbers.yaml bytes: config order x LAYERS order, raw ints.

    A package's `cordis` layer (programme-keyed EU contributions) is emitted
    last when present; keys are sorted so re-runs stay byte-identical. The
    `som` layer carries the derived `share_of_tam` key (issue-57 D-23,
    table-cell-only, emitted with `:.1f` — one decimal per issue-59 WI-22).
    The top-level `platform` block (issue-57 WI-16) is
    emitted after the six packages in revenue/referral/profiles order.
    """
    cordis = cordis or {}
    platform = platform or {}
    shares = shares or {}
    lines = [
        "# GENERATED by scripts/make_numbers.py from data/processed/market_sizing.csv,",
        "# data/processed/cordis_funding.csv and data/processed/platform_revenue.csv",
        "# (fixed-seed Monte Carlo, issue-53 D-05/D-11; platform block issue-57",
        "# WI-16/D-22). Do not edit by hand.",
        "# Keys mirror the prose placeholders: {{pX.layer.quantile}} and",
        "# {{platform.revenue.quantile}}; {{pX.som.share_of_tam}} is table-cell-only.",
        "# Display (issue-59 WI-20/WI-22): money renders € + K/M/B magnitude +",
        "# one decimal in prose AND tables; share_of_tam is a one-decimal percent.",
    ]
    for name, prefix in pairs:
        sub = df[df["package"] == name]
        if sub.empty:
            raise ValueError(f"package {name!r} has no market_sizing rows")
        lines.append(f"{prefix}:")
        for layer in LAYERS:
            lrow = sub[sub["layer"] == layer]
            if lrow.empty:
                continue  # absent layer: no key, never a zero (D-03 discipline)
            rec = lrow.iloc[0]
            cells = ", ".join(f"{col}: {int(str(rec[col]))}" for col in NUMERIC_COLS)
            if layer == "som":
                cells += f", share_of_tam: {shares[name]:.1f}"
            lines.append(f"  {layer}: {{{cells}}}")
        if name in cordis:
            cells = ", ".join(f"{k}: {v}" for k, v in sorted(cordis[name].items()))
            lines.append(f"  cordis: {{{cells}}}")
    if platform:
        lines.append("platform:")
        for key in PLATFORM_BLOCK_ORDER:
            cells = ", ".join(f"{q}: {platform[key][q]}"
                              for q in ("p10", "p50", "p90"))
            lines.append(f"  {key}: {{{cells}}}")
    return "\n".join(lines) + "\n"


def write_yaml(text: str, out_path: Path) -> Path:
    """Plain overwrite, byte-stable (utf-8, \\n, single trailing newline)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return out_path


def main(*, processed_dir: Path | str | None = None,
         out_path: Path | str | None = None,
         config_path: Path | str | None = None) -> int:
    """Write report/generated/numbers.yaml from the processed sizing table."""
    processed = _resolve(processed_dir, PROCESSED_DIR)
    out = _resolve(out_path, OUT_PATH)
    pairs = load_package_prefixes(_resolve(config_path, PACKAGES_PATH))
    df = read_market_sizing(processed / "market_sizing.csv", pairs)
    cordis = read_cordis_funding(processed / "cordis_funding.csv", pairs)
    platform = read_platform_revenue(processed / "platform_revenue.csv")
    shares = derive_share_of_tam(df, pairs)
    path = write_yaml(render_yaml(df, pairs, cordis, platform, shares), out)
    print(f"wrote {path} ({len(pairs)} packages + platform block)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
