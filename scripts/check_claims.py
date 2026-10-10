"""Trial-claims gate for the report prose and the evidence map (issue-58, D-26/D-27).

The repo has no test runner, so this committed
gate script substitutes for one, following the one-file-per-domain precedent
of check_tams.py. Run:

    uv run python scripts/check_claims.py

Prints `G-<n> PASS|FAIL` per gate and exits non-zero if any gate fails.
Zero network access; reads only report/*.md (top level, the seven sections)
and config/evidence_map.yaml — the same scope shape as check_prose.py.

The rules (issue-58 spec, rulings R-1-A/R-2/R-3):
- G-1  required needles: the P6 G.E.M. is framed as a *pre-screen* with the
  NC3Rs validation timeline (Dec 2024–Nov 2027) in 02_market.md and
  00_summary.md, and the mouse ED50 is named as the WHO-approved method in
  02_market.md (AC1, AC2).
- G-2  overlap disclosure: 03_industry.md discloses the P3/P4 and P5/P6
  shared-record situations and states the never-summed rule (AC4, R-1-A).
- G-3  tripwire: the five banned affirmative phrases are absent from all
  seven sections (AC3, D-26). Exact-phrase matching only — no loose
  trial…demand regexes — so the compliant negations stay green.
- G-4  claim anchors: no ctgov entry in config/evidence_map.yaml carries a
  banned affirmative phrase (the p2 negation basis passes).
- G-INT fixture self-test: a synthetic violating corpus fires every banned
  phrase, the needle detectors fire on a corpus missing the positioning and
  disclosure text, the evidence-map detector fires on a poisoned claim and
  passes a p2-style negation basis; the compliant-negation corpus produces
  zero banned hits and zero missing needles. Mirrors check_prose.py G-7 and
  check_tams.py G-INT discipline.

Banned phrases are matched case-insensitively on whitespace-normalized text
(line wraps must not hide a violation). "demand signals" also contains
"demand signal" as a substring; the fixture expectation is computed the same
way the detector works, so the overlap is consistent, not a bug.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

SECTIONS = (
    "00_summary.md", "01_customer.md", "02_market.md", "03_industry.md",
    "04_competitive.md", "A1_methods.md", "A2_validation_plan.md",
)

# G-3 exact list, pinned here (spec "Files to Create"): affirmative
# demand-framing phrases. Negations ("not a demand forecast", "do not
# measure training demand", "never evidence of commercial demand"), the
# "demand pool (V6)" interview reference and the product name "on-demand"
# contain none of these and stay green.
BANNED = (
    "active demand",
    "demand signal",
    "demand signals",
    "shows demand",
    "evidence of demand",
)

# Independently pinned copy of the G-3 ban list. The G-INT self-test asserts
# set(BANNED) == EXPECTED_BANNED so a silently weakened BANNED tuple fails
# the gate instead of the expectation being derived from BANNED itself.
EXPECTED_BANNED = {
    "active demand",
    "demand signal",
    "demand signals",
    "shows demand",
    "evidence of demand",
}

# G-1 needles (all matched case-insensitively on normalized text).
PRE_SCREEN = "pre-screen"
TIMELINE_RX = re.compile(r"Dec(?:ember)?\s*2024\s*[\u2013\u2014-]\s*Nov(?:ember)?\s*2027", re.I)
# "mouse ED50" named as the *only* WHO-approved method within one sentence
# (no period between), so the exclusivity framing cannot be silently dropped.
ED50_WHO_RX = re.compile(r"mouse ED50(?:(?!\.)[\s\S]){0,200}?only WHO-approved", re.I)

# G-2 needles (03_industry.md): P3/P4 disclosure (any accepted wording),
# P5/P6 disclosure, and the never-summed rule.
P3P4_RX = re.compile(
    r"all 216 chemistry studies also appear in the expertise record"
    r"|P4's entire record sits inside P3's"
    r"|P4's chemistry query is a subset of P3's expertise query",
    re.I,
)
P5P6_RX = re.compile(r"nine of P6's 10 studies", re.I)
NEVER_SUMMED = "must not be summed"


def normalize(text: str) -> str:
    """Collapse whitespace so line wraps cannot hide needles or phrases."""
    return re.sub(r"\s+", " ", text)


def banned_hits(text: str) -> list[str]:
    """Banned phrases present in one (normalized) text."""
    low = text.lower()
    return [p for p in BANNED if p in low]


def missing_needles_market(text: str) -> list[str]:
    low = text.lower()
    missing = []
    if PRE_SCREEN not in low:
        missing.append("'pre-screen' positioning")
    if not TIMELINE_RX.search(text):
        missing.append("NC3Rs timeline (Dec 2024–Nov 2027)")
    if not ED50_WHO_RX.search(text):
        missing.append("mouse ED50 named as WHO-approved")
    return missing


def missing_needles_summary(text: str) -> list[str]:
    low = text.lower()
    missing = []
    if PRE_SCREEN not in low:
        missing.append("'pre-screen' positioning")
    if not TIMELINE_RX.search(text):
        missing.append("NC3Rs timeline (Dec 2024–Nov 2027)")
    return missing


def missing_disclosures(text: str) -> list[str]:
    low = text.lower()
    missing = []
    if not P3P4_RX.search(text):
        missing.append("P3/P4 overlap disclosure")
    if not P5P6_RX.search(text):
        missing.append("P5/P6 overlap disclosure")
    if NEVER_SUMMED not in low:
        missing.append("never-summed rule")
    return missing


def ctgov_claim_violations(entries: list[dict]) -> list[str]:
    """G-4: scan the claim/basis text of every ctgov entry in the evidence map."""
    violations = []
    for entry in entries:
        if entry.get("source") != "ctgov":
            continue
        package = entry.get("package", "?")
        text = str(entry.get("claim") or entry.get("basis") or "")
        for phrase in banned_hits(normalize(text)):
            violations.append(f"{package} ctgov claim contains '{phrase}'")
    return violations


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
    files = {name: REPO_ROOT / "report" / name for name in SECTIONS}

    # G-0: all seven sections exist and the evidence map is present.
    missing_files = [n for n, p in files.items() if not p.is_file() or p.stat().st_size == 0]
    map_path = REPO_ROOT / "config" / "evidence_map.yaml"
    if not map_path.is_file():
        missing_files.append("config/evidence_map.yaml")
    if missing_files:
        print(f"G-0 FAIL: missing or empty inputs: {missing_files}")
        return 1
    print("G-0 PASS")

    texts = {n: normalize(files[n].read_text(encoding="utf-8")) for n in SECTIONS}

    # G-1: required positioning needles (AC1, AC2).
    needle_violations = (
        [f"[02_market.md] missing {m}" for m in missing_needles_market(texts["02_market.md"])]
        + [f"[00_summary.md] missing {m}" for m in missing_needles_summary(texts["00_summary.md"])]
    )
    ok = report("G-1", needle_violations,
                "pre-screen + NC3Rs timeline in Market/Summary; mouse ED50 as WHO-approved in Market") and ok

    # G-2: overlap disclosure in Industry (AC4, R-1-A).
    ok = report("G-2", [f"[03_industry.md] missing {m}" for m in missing_disclosures(texts["03_industry.md"])],
                "P3/P4 and P5/P6 overlap disclosed with the never-summed rule") and ok

    # G-3: banned affirmative phrases absent across the seven sections (AC3).
    phrase_hits = []
    for n in SECTIONS:
        for phrase in banned_hits(texts[n]):
            idx = texts[n].lower().index(phrase)
            snippet = texts[n][max(0, idx - 40):idx + len(phrase) + 40]
            phrase_hits.append(f"[{n}] '{phrase}': …{snippet}…")
    ok = report("G-3", phrase_hits, "no trial count framed as commercial demand evidence") and ok

    # G-4: evidence_map ctgov claim anchors carry no banned phrase.
    entries = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    ok = report("G-4", ctgov_claim_violations(entries), "ctgov claim anchors are context-only") and ok

    # G-INT: fixture self-test — the detectors fire on synthetic violations
    # and stay silent on the compliant negations (check_prose.py G-7 style).
    int_failures: list[str] = []

    # 1. Violating corpus: every banned phrase must be detected.
    bad_prose = normalize(" ".join([
        "The trial record shows active demand, not captive demand.",
        "Antivenom trials are the demand signal for screening capacity.",
        "Peptide pipelines demand signals of expertise growth.",
        "The registry shows demand growth across the chain.",
        "These counts are evidence of demand for the service.",
    ]))
    if set(BANNED) != EXPECTED_BANNED:
        int_failures.append(
            f"BANNED list drifted from the pinned expectation: "
            f"expected {sorted(EXPECTED_BANNED)}, found {sorted(set(BANNED))}"
        )
    fired = set(banned_hits(bad_prose))
    if fired != EXPECTED_BANNED:
        int_failures.append(
            f"violating corpus did not exercise every banned phrase: "
            f"expected {sorted(EXPECTED_BANNED)}, fired {sorted(fired)}"
        )

    # 2. Missing-needle corpus: needle detectors must fire with named items.
    empty = normalize("The G.E.M. screening service sells per-screen fees.")
    for label, detector in (("market", missing_needles_market),
                            ("summary", missing_needles_summary)):
        miss = detector(empty)
        if not miss:
            int_failures.append(f"{label} needle detector did not fire on a corpus without positioning")
    if missing_disclosures(normalize("P3 shows 325 completed and 62 recruiting.")) == []:
        int_failures.append("disclosure detector did not fire on a corpus without overlap disclosure")

    # 3. Evidence-map detector: poisoned claim fails, p2-style negation passes.
    poisoned = ctgov_claim_violations([
        {"package": "zeta_pkg", "source": "ctgov", "role": "context",
         "claim": "antivenom trials are the demand signal for screening capacity"},
    ])
    if len(poisoned) != 1 or "demand signal" not in poisoned[0]:
        int_failures.append(f"evidence-map detector missed a poisoned claim: {poisoned}")
    clean = ctgov_claim_violations([
        {"package": "alpha_pkg", "source": "ctgov", "role": "n/a",
         "basis": "clinical-trial counts do not measure training demand"},
        {"package": "beta_pkg", "source": "ctgov", "role": "context",
         "claim": "trial activity is ecosystem context; never evidence of commercial demand"},
        {"package": "gamma_pkg", "source": "openalex", "role": "driver",
         "claim": "institutions anchor the lab count"},
    ])
    if clean:
        int_failures.append(f"evidence-map detector false-positived: {clean}")

    # 4. Compliant-negation corpus: zero banned hits (UT-2 false-positive guard).
    good_prose = normalize(" ".join([
        "This is a limit on delivery, not a demand forecast.",
        "Trial counts do not measure training demand.",
        "The demand pool (V6) is the open question.",
        "EMBL-EBI Training (on-demand) offers free tutorials.",
        "Registry counts are context only, never evidence of commercial demand.",
        "The records overlap completely and the counts must not be summed.",
    ]))
    false_hits = banned_hits(good_prose)
    if false_hits:
        int_failures.append(f"banned-phrase detector false-positived on compliant negations: {false_hits}")

    # 5. Full-needle corpus: positioning + disclosure detectors pass.
    good_positioning = normalize(
        "The service is sold as a pre-screen while the NC3Rs-funded project "
        "runs December 2024–November 2027; the mouse ED50 route — the only "
        "WHO-approved potency assay — stays the regulatory standard."
    )
    good_disclosure = normalize(
        "All 216 chemistry studies also appear in the expertise record; nine of "
        "P6's 10 studies sit in P5's 35; the counts must not be summed."
    )
    if missing_needles_market(good_positioning) or missing_needles_summary(good_positioning):
        int_failures.append("needle detectors false-positived on a fully positioned corpus")
    if missing_disclosures(good_disclosure):
        int_failures.append(f"disclosure detector false-positived: {missing_disclosures(good_disclosure)}")

    if int_failures:
        print("G-INT FAIL: fixture self-test:")
        for f in int_failures:
            print(f"    {f}")
        ok = False
    else:
        print("G-INT PASS")

    print("all gates PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
