"""Ad-hoc verification gate for the report numbers (issue-53 D-11/WI-10;
platform block and derived shares added by issue-57 WI-16/D-23).

Three consistency checks:

1. report/generated/numbers.yaml mirrors data/processed/market_sizing.csv
   cell-for-cell for the sizing layers (derived keys like `share_of_tam`
   exempted from the cell-compare and RECOMPUTED from the csv instead),
   data/processed/cordis_funding.csv for the per-package `cordis` layers
   (keys pX.layer.quantile, raw integers), and data/processed/
   platform_revenue.csv for the top-level `platform` block (total → revenue);
2. every {{pX.layer.quantile}} and {{platform.layer.quantile}} placeholder in
   the seven hand-written report sections resolves in numbers.yaml, no
   malformed {{…}} fragment survives, and {{pX.som.share_of_tam}} appears
   ONLY on table rows (the prose money renderer would format it as €);
3. the built docx (if present) contains no literal "{{" and no literal "[@"
   (the citeproc tripwire: a Makefile edit that drops --citeproc would leak
   citation keys), and every placeholder's rendering — the money_display €
   form (prose and tables alike per issue-59 WI-20) or the share's
   one-decimal string — appears in the document text.

Run:

    uv run python scripts/check_numbers.py

Prints `G-<n> PASS|FAIL` per gate and exits non-zero if any gate fails.
Zero network access; the docx round-trip (G-INT) is skipped with a visible
note when build/vmg2_draft.docx is absent (fresh clone before `make report`
— AC-1's `make all && make check` runs it built). The placeholder grammar
and the € renderer mirror report/filters/numbers.lua exactly
((p[1-9]|platform) / letters layer / letter-then-word-or-underscore
quantile).
"""

from __future__ import annotations

import csv
import re
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

NUMBERS = REPO_ROOT / "report" / "generated" / "numbers.yaml"
SIZING = REPO_ROOT / "data" / "processed" / "market_sizing.csv"
CORDIS = REPO_ROOT / "data" / "processed" / "cordis_funding.csv"
PLATFORM = REPO_ROOT / "data" / "processed" / "platform_revenue.csv"
DOCX = REPO_ROOT / "build" / "vmg2_draft.docx"
SECTIONS = (
    "00_summary.md", "01_customer.md", "02_market.md", "03_industry.md",
    "04_competitive.md", "A1_methods.md", "A2_validation_plan.md",
)

# Mirror of the Lua grammar: {{(p[1-9]|platform).(%a+).(%a[%w_]*)}} — the
# platform alternation is two gsubs in numbers.lua (Lua patterns have no
# alternation); this regex is the third mirror (issue-57 WI-16).
PLACEHOLDER_RE = re.compile(
    r"\{\{(p[1-9]|platform)\.([A-Za-z]+)\.([A-Za-z][A-Za-z0-9_]*)\}\}")
ANY_BRACE_RE = re.compile(r"\{\{[^{}]*\}\}|\{\{|\}\}")
# Derived keys (issue-57 D-23): computed by make_numbers, never CSV cells.
DERIVED_SOM_KEYS = ("share_of_tam",)
# platform_revenue.csv component -> numbers.yaml platform key.
PLATFORM_COMPONENT_KEYS = {"profiles": "profiles", "referral": "referral",
                           "total": "revenue"}


def share_prose_violations(source: str, text: str) -> list[str]:
    """{{pX.som.share_of_tam}} is table-cell-only (issue-57 D-23).

    The Lua renderer money-formats every prose placeholder (the €
    money_display form), so a share used in prose would leak as a euro
    amount. Statically: the placeholder may only appear on a markdown table
    row (a line whose first non-space character is `|`). Used by G-3 over
    the real sections and by G-5 over its negative fixture.
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in PLACEHOLDER_RE.finditer(line):
            if m.group(3) in DERIVED_SOM_KEYS and not line.lstrip().startswith("|"):
                out.append(f"[{source}:L{lineno}] {m.group(0)} used outside a "
                           f"table row (prose would render it as €)")
    return out


def load_numbers(path: Path) -> dict[str, dict[str, dict[str, int | float]]]:
    """Parse numbers.yaml with the same grammar the Lua filter uses:
    top-level `pN:` or `platform:` lines, then `  layer: {key: value, ...}`
    flow maps. Value atoms are raw integers, except the derived
    `share_of_tam`, which carries a one-decimal float (issue-59 WI-22
    grammar v2 — the third parser mirror, alongside numbers.lua and the
    make_numbers.py writer). A flow-map segment matching neither atom raises
    ValueError loudly (the G-5 malformed-share fixture) rather than
    silently truncating `0,1` to `0`."""
    numbers: dict[str, dict[str, dict[str, int | float]]] = {}
    cur = None
    for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("#"):
            continue
        pkg = re.match(r"^(p[1-9]|platform):\s*$", line)
        if pkg:
            cur = pkg.group(1)
            numbers[cur] = {}
            continue
        layer = re.match(r"^  ([A-Za-z]+): \{(.+)\}\s*$", line)
        if layer and cur:
            body: dict[str, int | float] = {}
            for seg in layer.group(2).split(","):
                m = re.fullmatch(
                    r"\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(-?\d+(?:\.\d+)?)\s*",
                    seg)
                if not m:
                    raise ValueError(
                        f"{path.name}: malformed flow-map segment {seg!r} in "
                        f"layer {layer.group(1)!r} (line {lineno})")
                k, v = m.group(1), m.group(2)
                body[k] = float(v) if "." in v else int(v)
            numbers[cur][layer.group(1)] = body
    return numbers


def money_display(n: int) -> str:
    """Mirror of numbers.lua money_display (issue-59 WI-20, replacing
    money_2sf): € + K/M/B magnitude + exactly one decimal at that magnitude;
    values below €1,000 render as a plain integer €; 0 renders "€0"; the
    sign is preserved. One formatter serves prose AND table cells now (the
    raw-integer table rendering of Appendix B rule 4 is retired)."""
    if n == 0:
        return "€0"
    sign = "-" if n < 0 else ""
    a = abs(n)
    suffix, mag = "", 1
    if a >= 1_000_000_000:
        suffix, mag = "B", 1_000_000_000
    elif a >= 1_000_000:
        suffix, mag = "M", 1_000_000
    elif a >= 1_000:
        suffix, mag = "K", 1_000
    if mag == 1:
        return f"{sign}€{a}"
    # K/M boundary guard (issue-59 review): a mantissa that rounds to
    # 1000.0 at the current magnitude must promote to the next magnitude,
    # so values in [999,950, 999,999] render "€1.0M", never "€1000.0K".
    # B is the top suffix in the display contract, so it is not promoted.
    if mag < 1_000_000_000 and f"{a / mag:.1f}" == "1000.0":
        if mag == 1_000:
            suffix, mag = "M", 1_000_000
        else:
            suffix, mag = "B", 1_000_000_000
    return f"{sign}€{a / mag:.1f}{suffix}"


def report(gate: str, violations: list[str], context: str) -> bool:
    if violations:
        print(f"{gate} FAIL ({context}):")
        for v in violations[:12]:
            print(f"    {v}")
        if len(violations) > 12:
            print(f"    … {len(violations) - 12} more")
        return False
    print(f"{gate} PASS")
    return True


def main() -> int:
    ok = True

    # G-1: numbers.yaml parses and carries the locked shape — six packages,
    # each layer with p10/p50/p90 plus the envelope columns (som additionally
    # carries the derived share_of_tam), and the platform block with
    # revenue/referral/profiles × p10/p50/p90 (issue-57 WI-16).
    try:
        numbers = load_numbers(NUMBERS)
    except ValueError as exc:
        ok = report("G-1", [str(exc)], "numbers.yaml parse") and ok
        numbers = {}
    violations = []
    for pkg in (f"p{i}" for i in range(1, 7)):
        if pkg not in numbers:
            violations.append(f"{pkg} absent from numbers.yaml")
            continue
        for layer, body in numbers[pkg].items():
            if layer == "cordis":
                # CORDIS EU contributions are programme-keyed, not quantiles.
                for q in ("h2020", "horizon"):
                    if q not in body:
                        violations.append(f"{pkg}.cordis.{q} missing")
                continue
            for q in ("p10", "p50", "p90", "envelope_low", "envelope_high"):
                if q not in body:
                    violations.append(f"{pkg}.{layer}.{q} missing")
            if layer == "som" and "share_of_tam" not in body:
                violations.append(f"{pkg}.som.share_of_tam missing (derived key)")
            if not (body.get("p10", 0) <= body.get("p50", 0) <= body.get("p90", 0)):
                violations.append(f"{pkg}.{layer} quantiles not monotonic")
            if "envelope_low" in body and "p10" in body:
                if not (body["envelope_low"] <= body["p10"] and body["p90"] <= body["envelope_high"]):
                    violations.append(f"{pkg}.{layer} quantiles outside the bound-wise envelope")
    platform_block = numbers.get("platform", {})
    for key in ("revenue", "referral", "profiles"):
        body = platform_block.get(key)
        if not body:
            violations.append(f"platform.{key} absent from numbers.yaml")
            continue
        for q in ("p10", "p50", "p90"):
            if q not in body:
                violations.append(f"platform.{key}.{q} missing")
        if not (body.get("p10", 0) <= body.get("p50", 0) <= body.get("p90", 0)):
            violations.append(f"platform.{key} quantiles not monotonic")
    ok = report("G-1", violations, "numbers.yaml shape") and ok

    # G-2: numbers.yaml sizing layers == market_sizing.csv, cell-for-cell,
    # both directions — after EXEMPTING the derived som keys (share_of_tam,
    # recomputed from the csv instead) and the top-level platform block
    # (mirrored against platform_revenue.csv in G-2c). The `cordis` layers
    # are checked in G-2b, not against the sizing table.
    violations = []
    csv_cells: dict[str, dict[str, int]] = {}
    with SIZING.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pkg = row["package"].split("_", 1)[0]
            csv_cells.setdefault(pkg, {})[row["layer"]] = {
                q: int(row[q]) for q in
                ("p10", "p50", "p90", "envelope_low", "envelope_high")
            }
    numbers_sizing = {
        pkg: {layer: {q: v for q, v in body.items() if q not in DERIVED_SOM_KEYS}
              for layer, body in layers.items() if layer != "cordis"}
        for pkg, layers in numbers.items() if pkg != "platform"
    }
    if csv_cells != numbers_sizing:
        only_yaml = sorted(set(numbers_sizing) - set(csv_cells))
        only_csv = sorted(set(csv_cells) - set(numbers_sizing))
        diffs = []
        for pkg in sorted(set(csv_cells) & set(numbers_sizing)):
            for layer in sorted(set(csv_cells[pkg]) | set(numbers_sizing[pkg])):
                if csv_cells[pkg].get(layer) != numbers_sizing[pkg].get(layer):
                    diffs.append(f"{pkg}.{layer}")
        violations.append(f"yaml-only packages {only_yaml}; csv-only packages {only_csv}; "
                          f"cell diffs {diffs}")
    # Derived-key recompute (issue-57 D-23, one-decimal precision per
    # issue-59 WI-22): share_of_tam == round(som.p50 / tam.p50 * 100, 1)
    # over the CSV cells, for all six packages, compared with a 0.05
    # tolerance (the emitted atom is one-decimal; the tolerance absorbs
    # float representation error, nothing else). A positive som must never
    # round to a 0.0 share — the readable form of a positive ceiling is at
    # least 0.1.
    for pkg in sorted(csv_cells):
        som = csv_cells[pkg].get("som")
        tam = csv_cells[pkg].get("tam")
        if not som or not tam:
            violations.append(f"{pkg}: share_of_tam needs both som and tam rows in the csv")
            continue
        if tam["p50"] == 0:
            violations.append(f"{pkg}: tam.p50 is 0 — share_of_tam undefined")
            continue
        want = round(som["p50"] / tam["p50"] * 100, 1)
        if som["p50"] > 0 and want == 0.0:
            violations.append(f"{pkg}: positive som.p50 {som['p50']} rounds the share "
                              f"to 0.0 — a delivery ceiling must never display as 0")
        got = numbers.get(pkg, {}).get("som", {}).get("share_of_tam")
        if got is None or abs(got - want) > 0.05:
            violations.append(f"{pkg}.som.share_of_tam = {got}, expected "
                              f"round({som['p50']}/{tam['p50']}*100, 1) = {want}")
    ok = report("G-2", violations, "numbers.yaml mirrors market_sizing.csv "
                                   "(derived keys exempted + recomputed)") and ok

    # G-2c: the numbers.yaml `platform` block mirrors platform_revenue.csv
    # cell-for-cell (component total is exposed as the yaml key `revenue`).
    violations = []
    platform_cells: dict[str, dict[str, int]] = {}
    with PLATFORM.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = PLATFORM_COMPONENT_KEYS.get(row["component"])
            if key is None:
                violations.append(f"platform_revenue.csv: unknown component "
                                  f"{row['component']!r}")
                continue
            platform_cells[key] = {q: int(row[q]) for q in ("p10", "p50", "p90")}
    if platform_cells != platform_block:
        violations.append(f"platform cells: yaml {platform_block}; csv {platform_cells}")
    ok = report("G-2c", violations, "numbers.yaml mirrors platform_revenue.csv") and ok

    # G-2b: numbers.yaml `cordis` layers mirror cordis_funding.csv — the EU
    # contribution rounded to a raw integer, programme label lowercased.
    violations = []
    cordis_cells: dict[str, dict[str, int]] = {}
    with CORDIS.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pkg = row["package"].split("_", 1)[0]
            cordis_cells.setdefault(pkg, {})[row["programme"].lower()] = \
                int(round(float(row["eu_contribution_eur"])))
    numbers_cordis = {pkg: layers["cordis"] for pkg, layers in numbers.items()
                      if "cordis" in layers}
    if cordis_cells != numbers_cordis:
        violations.append(f"cordis cells: yaml {numbers_cordis}; csv {cordis_cells}")
    ok = report("G-2b", violations, "numbers.yaml mirrors cordis_funding.csv") and ok

    # G-3: every placeholder in the seven real sections resolves; no
    # malformed brace fragment survives; {{pX.som.share_of_tam}} appears
    # only on table rows (issue-57 D-23 table-cell-only rule).
    violations = []
    all_placeholders: set[tuple[str, str, str]] = set()
    for name in SECTIONS:
        text = (REPO_ROOT / "report" / name).read_text(encoding="utf-8")
        for m in ANY_BRACE_RE.finditer(text):
            frag = m.group(0)
            full = PLACEHOLDER_RE.fullmatch(frag)
            if not full:
                violations.append(f"[{name}] malformed placeholder fragment: {frag}")
                continue
            pkg, layer, q = full.groups()
            all_placeholders.add((pkg, layer, q))
            if q not in numbers.get(pkg, {}).get(layer, {}):
                violations.append(f"[{name}] unresolved {{{{{pkg}.{layer}.{q}}}}}")
        violations += share_prose_violations(name, text)
    ok = report("G-3", violations, "placeholders resolve in numbers.yaml; "
                                   "shares table-only") and ok

    # G-4: the money_display renderer matches the issue-59 WI-20 contract on
    # the spec's own examples, magnitude boundaries and the plain-€ range.
    violations = []
    cases = {
        140_496: "€140.5K", 8_961: "€9.0K", 13_002: "€13.0K",
        94_112_351: "€94.1M", 84_000_000: "€84.0M", 1_375_472_759: "€1.4B",
        999: "€999", 0: "€0", -1_600_000: "-€1.6M",
        1_000: "€1.0K", 1_000_000: "€1.0M", 1_000_000_000: "€1.0B",
        999_960: "€1.0M", 999_999_999: "€1.0B", 999_949: "€999.9K",
    }
    for n, want in cases.items():
        got = money_display(n)
        if got != want:
            violations.append(f"money_display({n}) = {got}, expected {want}")
    ok = report("G-4", violations, "money_display € rendering") and ok

    # G-5: negative fixtures — an unresolved placeholder, a malformed
    # fragment and a share used in prose must be caught by the same logic
    # G-3 applies (issue-57: platform ghost + malformed {{platform.}} +
    # prose share_of_tam guard cases).
    violations = []
    fixture = "Real {{p5.som.p50}} plus ghost {{p9.som.p50}} and broken {{p5.nope.p10}} " \
              "and mangled {{p5.som.}} tail. Ghost {{platform.ghost.p1}} and mangled " \
              "{{platform.}} tail. Prose {{p1.som.share_of_tam}} guard."
    found = set()
    unresolved_names: set[str] = set()
    malformed_names: set[str] = set()
    for m in ANY_BRACE_RE.finditer(fixture):
        frag = m.group(0)
        full = PLACEHOLDER_RE.fullmatch(frag)
        if not full:
            found.add("malformed")
            malformed_names.add(frag)
        elif full.group(3) not in numbers.get(full.group(1), {}).get(full.group(2), {}):
            found.add("unresolved")
            unresolved_names.add(frag)
    if "malformed" not in found:
        violations.append("{{p5.som.}} fragment not detected as malformed")
    if "unresolved" not in found:
        violations.append("ghost/absent-layer placeholders not detected")
    if PLACEHOLDER_RE.fullmatch("{{p5.som.p50}}") is None:
        violations.append("valid grammar rejected by the mirror regex")
    # platform grammar mirrors (issue-57 WI-16):
    if PLACEHOLDER_RE.fullmatch("{{platform.revenue.p10}}") is None:
        violations.append("valid platform grammar rejected by the mirror regex")
    if "{{platform.ghost.p1}}" not in unresolved_names:
        violations.append("{{platform.ghost.p1}} not detected as unresolved")
    if "{{platform.}}" not in malformed_names:
        violations.append("{{platform.}} not detected as malformed")
    # table-only guard fires on the prose share (the fixture line is prose):
    if not share_prose_violations("fixture", fixture):
        violations.append("prose {{p1.som.share_of_tam}} not caught by the "
                          "table-only guard")
    # and stays silent on the table-row form:
    if share_prose_violations("fixture", "| P1 | {{p1.som.share_of_tam}} |"):
        violations.append("table-row share wrongly flagged by the guard")
    # issue-59 WI-22 / V-1: decimal share fixtures — a well-formed
    # one-decimal share parses to a float whose `:.1f` rendering is the
    # committed atom ("0.1", never "0" and never bare-tostring drift), the
    # neighbouring quantile cells stay raw integers, and a malformed comma
    # atom (`share_of_tam: 0,1`) trips the parser loudly instead of
    # silently truncating to 0. These fixtures exercise only the Python
    # load_numbers mirror: G-INT pins the Lua twin's VALID share rendering
    # (its one-decimal string in the docx), but numbers.lua's own
    # malformed-input scanner (the `error` on a leftover "{{") is not run
    # by any gate here.
    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "good.yaml"
        good.write_text(
            "p1:\n  som: {p10: 8961, p50: 13002, p90: 18149, envelope_low: 4800, "
            "envelope_high: 26400, share_of_tam: 0.1}\n", encoding="utf-8")
        parsed = load_numbers(good)["p1"]["som"]
        if parsed.get("share_of_tam") != 0.1 or f"{parsed.get('share_of_tam'):.1f}" != "0.1":
            violations.append("decimal share_of_tam fixture did not parse to "
                              "0.1 / render '0.1'")
        if parsed.get("p50") != 13002 or not isinstance(parsed.get("p50"), int):
            violations.append("quantile cell next to a decimal share is no longer a raw int")
        bad = Path(td) / "bad.yaml"
        bad.write_text(
            "p1:\n  som: {p10: 8961, p50: 13002, p90: 18149, envelope_low: 4800, "
            "envelope_high: 26400, share_of_tam: 0,1}\n", encoding="utf-8")
        try:
            load_numbers(bad)
            violations.append("malformed share_of_tam '0,1' parsed silently")
        except ValueError:
            pass
    ok = report("G-5", violations, "negative fixtures trip the detector") and ok

    # G-INT: docx round-trip — no leaked {{ or [@, and every placeholder
    # renders in its formatted form (money_display for monetary keys, prose
    # and tables alike; the one-decimal string for share cells). No
    # raw-integer acceptance since issue-59 WI-20 retired the exact-table
    # rendering of Appendix B rule 4.
    if not DOCX.exists():
        print("G-INT PASS (skip: build/vmg2_draft.docx absent — run `make report` "
              "to exercise the round-trip)")
    else:
        xml = zipfile.ZipFile(DOCX).read("word/document.xml").decode("utf-8")
        text = re.sub(r"<[^>]+>", "", xml)
        violations = []
        if "{{" in text:
            violations.append("literal {{ survived into the docx (unresolved placeholder)")
        if "[@" in text:
            violations.append("literal [@ survived into the docx (citeproc not applied?)")
        for pkg, layer, q in sorted(all_placeholders):
            if q not in numbers.get(pkg, {}).get(layer, {}):
                continue  # already failed in G-3
            v = numbers[pkg][layer][q]
            want = f"{v:.1f}" if q == "share_of_tam" else money_display(v)
            if want not in text:
                violations.append(f"{{{{{pkg}.{layer}.{q}}}}} does not render as "
                                  f"{want} in the docx")
        ok = report("G-INT", violations, "docx round-trip") and ok

    print("all gates PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
