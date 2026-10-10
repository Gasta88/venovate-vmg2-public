"""Ad-hoc verification gate for the report prose (issue-53 WI-10, D-10/D-16).

Enforces Appendix A of the issue-53 light spec: the banned-vocabulary list
over the SEVEN hand-written report sections, with the single exception line
"Allowed in A1_methods.md only: snapshot, manifest, file names" implemented
as: Annex 1 may use snapshot/manifest and may quote stored identifiers and
file names inside inline code spans; every other file may not. Also enforces
AC-7 (no [S#] markers, no keys, no DEFERRED outside Annex 1) and D-16 (the
"holds at the X layer" phrasing is banned). Issue-59 adds two decision
tripwires: G-8 (D-23 wording — the retired headline names "realistic annual
take" / "realistic revenue band" must produce zero hits across the seven
sections) and G-9 (D-28 scoping — the Nagoya / access-and-benefit-sharing
framing may only appear in sentences that also name the physical-sourcing
side; the global "shape every force" framing and the bare "Threat of new
entrants: Low" verdict heading are banned).

Run:

    uv run python scripts/check_prose.py

Prints `G-<n> PASS|FAIL` per gate and exits non-zero if any gate fails.
Zero network access; reads only report/*.md (top level). Generated tables
under report/generated/ are OUT OF SCOPE — they render stored identifiers
by design (biosecurity-scope precedent, decisions.md 2026-10-01).

Scope notes (deliberate, per the spec's edge-case list):
- Image embeds `![alt](path.png)` and link targets `[text](url)` are stripped
  before scanning: the embed syntax must carry file paths, and the visible
  prose is what Appendix A governs.
- Citeproc citations `[@key]` / `[@a; @b]` are stripped: keys are reference
  identifiers, not package keys.
- In A1_methods.md only, inline code spans `...` are stripped: that is the
  "file names" exception (filenames and quoted stored identifiers such as
  snapshot stems or query strings).
- `DEFERRED` is the all-caps marker the light spec bans OUTSIDE Annex 1
  (AC-7); lowercase English "deferred" is not the marker. `Notion` is the
  tool name; lowercase "notion" is ordinary English. Both are matched
  case-sensitively; every other term is matched case-insensitively.
- The G-7 negative corpus deliberately quotes the real six keys and every
  banned term to prove the detector fires; the positive corpus proves the
  A1 exception does not over-fire.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

SECTIONS = (
    "00_summary.md", "01_customer.md", "02_market.md", "03_industry.md",
    "04_competitive.md", "A1_methods.md", "A2_validation_plan.md",
)
ANNEX1 = "A1_methods.md"

# Appendix A verbatim, plus the words the exception line implies are banned
# outside A1 (snapshot) — each entry is (name, compiled regex). Word
# boundaries keep "system" from matching `stem`, "configuration" from
# matching `config`, "gatekeepers" from matching `gate`, and "honestly" from
# matching `honest`.
BANNED = (
    ("contract", re.compile(r"\bcontracts?\b", re.I)),
    ("byte-for-byte", re.compile(r"byte-for-byte", re.I)),
    ("byte-identical", re.compile(r"byte-identical", re.I)),
    ("issue-", re.compile(r"issue-", re.I)),
    ("issue #", re.compile(r"issue\s*#", re.I)),
    ("#<digits>", re.compile(r"#\d+")),
    ("decisions.md", re.compile(r"decisions\.md", re.I)),
    ("ruling", re.compile(r"\brulings?\b", re.I)),
    ("gate", re.compile(r"\bgates?\b", re.I)),
    ("home section", re.compile(r"\bhome\s+section\b", re.I)),
    ("path-referenced", re.compile(r"path[-\s]referenced\b", re.I)),
    ("path only", re.compile(r"\bpath\s+only\b", re.I)),
    ("re-spelled", re.compile(r"re-spell", re.I)),
    ("quoted exactly", re.compile(r"quoted\s+exactly", re.I)),
    ("single source of truth", re.compile(r"single\s+source\s+of\s+truth", re.I)),
    ("config", re.compile(r"\bconfig\b", re.I)),
    ("package model", re.compile(r"\bpackage\s+model\b", re.I)),
    ("assumptions inventory", re.compile(r"\bassumptions?\s+inventor(?:y|ies)\b", re.I)),
    ("snapshot coverage", re.compile(r"\bsnapshot\s+coverage\b", re.I)),
    ("the data underlying this", re.compile(r"the\s+data\s+underlying\s+this", re.I)),
    ("honest", re.compile(r"\bhonest\b", re.I)),
    ("E-8", re.compile(r"\bE-8\b")),
    ("E-9", re.compile(r"\bE-9\b")),
    ("EC-1", re.compile(r"\bEC-1\b")),
    ("per Maria", re.compile(r"\bper\s+Maria\b", re.I)),
    ("Notion", re.compile(r"\bNotion\b")),  # tool name, case-sensitive
    ("this session", re.compile(r"\bthis\s+session\b", re.I)),
    ("stem", re.compile(r"\bstems?\b", re.I)),
    ("manifest", re.compile(r"\bmanifests?\b", re.I)),
    ("determinism", re.compile(r"\bdeterminism\b", re.I)),
    ("erratum", re.compile(r"\berratum\b", re.I)),
    ("Lens.org", re.compile(r"Lens\.org", re.I)),
    (".csv", re.compile(r"\.csv\b", re.I)),
    (".md", re.compile(r"\.md\b", re.I)),
    (".png", re.compile(r"\.png\b", re.I)),
    (".yaml", re.compile(r"\.yaml\b", re.I)),
    ("snapshot", re.compile(r"\bsnapshots?\b", re.I)),  # banned outside A1 (exception line)
    ("DEFERRED", re.compile(r"DEFERRED")),             # banned outside Annex 1 (AC-7)
)
# A1 exception: snapshot, manifest and DEFERRED are allowed there; file
# names / stored identifiers are allowed inside inline code spans.
A1_ALLOW = {"snapshot", "manifest", "DEFERRED"}

# New and old package keys (D-01 renamed six; DoD-3 requires zero old keys).
KEY_RE = re.compile(
    r"\bp[1-6]_(?:venospatial|insilico_workshop|marketplace|peptide_service"
    r"|venomate|gem)\b"
    r"|\b(?:venomate|expertise_marketplace|peptide_chemistry_service"
    r"|training_school|multi_omics_pipeline|galleria_envenoming_model)\b"
)
# [S#] source markers (D-09) and the banned verdict phrasing (D-16).
SMARK_RE = re.compile(r"\[\s*S\d+\s*\]")
HOLDS_AT_RE = re.compile(r"\bholds at the\b", re.I)

# Issue-59 tripwires. G-8 (D-23 wording): the retired headline names must
# produce zero hits across the seven sections. G-9 (D-28 scoping): the
# Nagoya / benefit-sharing framing may only appear in sentences that also
# name the physical-sourcing side; the "shape every force" framing and the
# bare global "Threat of new entrants: Low" verdict heading are banned.
# The co-occurrence rule scans the three owning sections (00/03/04); the
# banned framing and heading scan all seven. references.yaml and the
# generated tables are out of scope (this gate reads report/*.md only).
D23_BANNED = ("realistic annual take", "realistic revenue band")
D28_BANNED_PHRASES = ("shape every force",)
D28_BARE_HEADLINE_RE = re.compile(
    r"^#{1,6}\s*Threat of new entrants: Low\s*$", re.I | re.M)
D28_TRIGGER_RE = re.compile(r"Nagoya|benefit-sharing", re.I)
D28_SOURCING_RE = re.compile(
    r"sourcing|animal|genetic resources|colon(?:y|ies)|venom collection", re.I)
D28_SCOPE_SECTIONS = ("00_summary.md", "03_industry.md", "04_competitive.md")

# Preprocessing patterns (shared by the scanner and the G-7 self-test).
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\[\]]*)\]\([^)]*\)")
CITE_RE = re.compile(r"\[[^\[\]]*@[^\[\]]*\]")
CODESPAN_RE = re.compile(r"`[^`]*`")
FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)


def prose_body(text: str, annex1: bool) -> str:
    """Strip embed/link/citation syntax (and code spans in A1) so only the
    hand-written prose remains for scanning."""
    text = FENCE_RE.sub(" ", text)
    text = IMAGE_RE.sub(" ", text)
    text = LINK_RE.sub(r"\1", text)
    text = CITE_RE.sub(" ", text)
    if annex1:
        text = CODESPAN_RE.sub(" ", text)
    return text


def scan_text(text: str, *, annex1: bool) -> list[str]:
    """Return human-readable violations found in one prose body."""
    violations: list[str] = []
    for name, rx in BANNED:
        if annex1 and name in A1_ALLOW:
            continue
        for m in rx.finditer(text):
            snippet = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
            violations.append(f"{name}: …{snippet}…")
    for rx, label in ((KEY_RE, "package key"), (SMARK_RE, "[S#] marker"),
                      (HOLDS_AT_RE, "'holds at the'")):
        for m in rx.finditer(text):
            snippet = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
            violations.append(f"{label}: …{snippet}…")
    return violations


def scan_file(path: Path) -> list[str]:
    text = prose_body(path.read_text(encoding="utf-8"), annex1=path.name == ANNEX1)
    return scan_text(text, annex1=path.name == ANNEX1)


def d23_wording_violations(source: str, text: str) -> list[str]:
    """G-8 detector: the D-23 retired headline wording, zero hits expected."""
    low = text.lower()
    return [f"[{source}] D-23 wording: {phrase!r}"
            for phrase in D23_BANNED if phrase in low]


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences for the G-9 co-occurrence rule: wrapped
    lines are joined inside each paragraph, then the paragraph is cut after
    terminal punctuation. Tuned against the final edited prose (V-3); the
    G-9 fixtures pin both directions."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        joined = re.sub(r"\s+", " ", para).strip()
        if joined:
            out.extend(re.split(r"(?<=[.!?])\s+", joined))
    return out


def d28_scoping_violations(source: str, raw: str, body: str) -> list[str]:
    """G-9 detector over one section: banned global framing and the bare
    verdict heading (D-28 readings the report must not carry), plus the
    sourcing co-occurrence rule over the prose body (citations stripped, so
    a bare [@nagoya] key cannot trigger it)."""
    violations = []
    flat = body.lower()
    for phrase in D28_BANNED_PHRASES:
        if phrase in flat:
            violations.append(f"[{source}] D-28 scoping: banned framing "
                              f"{phrase!r} (the barrier is sourcing-side only)")
    for m in D28_BARE_HEADLINE_RE.finditer(raw):
        violations.append(f"[{source}] D-28 scoping: global verdict heading "
                          f"{m.group(0).strip()!r} — the entrants verdict must "
                          f"be scoped per line")
    for s in split_sentences(body):
        if D28_TRIGGER_RE.search(s) and not D28_SOURCING_RE.search(s):
            violations.append(f"[{source}] D-28 scoping: Nagoya/benefit-sharing "
                              f"sentence without the sourcing side: "
                              f"…{s[:90]}…")
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

    # G-0: all seven hand-written sections exist and are non-empty.
    missing = [n for n, p in files.items() if not p.is_file() or p.stat().st_size == 0]
    if missing:
        print(f"G-0 FAIL: missing or empty sections: {missing}")
        ok = False
    else:
        print("G-0 PASS")

    # G-1…G-5: the banned-vocabulary rules over the six non-A1 sections.
    six = [n for n in SECTIONS if n != ANNEX1]
    rules = (
        ("G-1", "banned vocabulary", lambda t, a: [v for v in scan_text(t, annex1=a)
                                                   if not v.startswith(("package key", "[S#]", "'holds"))]),
        ("G-2", "[S#] markers", lambda t, a: [v for v in scan_text(t, annex1=a)
                                              if v.startswith("[S#]")]),
        ("G-3", "package keys", lambda t, a: [v for v in scan_text(t, annex1=a)
                                              if v.startswith("package key")]),
        ("G-4", "'holds at the'", lambda t, a: [v for v in scan_text(t, annex1=a)
                                                if v.startswith("'holds at the'")]),
        ("G-5", "DEFERRED outside Annex 1", lambda t, a: [v for v in scan_text(t, annex1=a)
                                                          if v.startswith("DEFERRED")]),
    )
    bodies = {n: prose_body(files[n].read_text(encoding="utf-8"), annex1=False)
              for n in six if files[n].is_file()}
    for gate, label, pick in rules:
        violations = []
        for n in six:
            if n in bodies:
                violations += [f"[{n}] {v}" for v in pick(bodies[n], False)]
        ok = report(gate, violations, label) and ok

    # G-6: A1_methods.md — full rule set minus its allowlist (snapshot,
    # manifest, DEFERRED; code spans stripped by prose_body).
    if files[ANNEX1].is_file():
        a1_body = prose_body(files[ANNEX1].read_text(encoding="utf-8"), annex1=True)
        ok = report("G-6", [f"[A1] {v}" for v in scan_text(a1_body, annex1=True)],
                    "A1 exception list enforced") and ok

    # G-7: fixture self-test — a synthetic violating corpus must produce
    # exactly the expected hits, and the synthetic A1-exception corpus
    # (snapshot/manifest/code-span identifiers) must be clean. Negative
    # fixtures keep this gate honest about its own regexes.
    with tempfile.TemporaryDirectory() as td:
        bad = "\n".join([
            "The contract is byte-for-byte and byte-identical.",
            "See issue-9 and issue #10 and #27 in decisions.md.",
            "The ruling gate-verified the home section path-referenced row.",
            "Path only, re-spelled, quoted exactly, single source of truth.",
            "The config and package model assumptions inventory show snapshot coverage.",
            "Honest: DEFERRED, E-8, E-9, EC-1, per Maria, Notion, this session.",
            "The stem manifest determinism erratum Lens.org file.csv table.md",
            "figure.png and config.yaml snapshots underlie the data underlying this analysis.",
            "p5_venomate and venomate [S12] holds at the tam layer.",
        ])
        hits = scan_text(bad, annex1=False)
        expected = ("contract", "byte-for-byte", "byte-identical", "issue-",
                    "issue #", "#<digits>", "decisions.md", "ruling", "gate",
                    "home section", "path-referenced", "path only", "re-spelled",
                    "quoted exactly", "single source of truth", "config",
                    "package model", "assumptions inventory", "snapshot coverage",
                    "the data underlying this", "honest", "E-8", "E-9", "EC-1",
                    "per Maria", "Notion", "this session", "stem", "manifest",
                    "determinism", "erratum", "Lens.org", ".csv", ".md", ".png",
                    ".yaml", "snapshot", "DEFERRED", "package key", "[S#] marker",
                    "'holds at the'")
        found = {h.split(":")[0] for h in hits}
        missing_terms = [t for t in expected if t not in found]
        good = "\n".join([
            "The 5 Oct 2026 snapshot sits beside its manifest file.",
            "`p5_venomate-publication_year.csv` and `config/queries.yaml`",
            "are stored identifiers quoted from the files; `decisions.md`",
            "records the owner decision dated 2026-10-04.",
            "![Evidence roles](figures/fig_coverage.png)",
            "Package email claims cite [@maria2026email] and [@grantletter].",
        ])
        false_hits = scan_text(prose_body(good, annex1=True), annex1=True)
        if missing_terms or false_hits:
            print("G-7 FAIL: fixture self-test:")
            if missing_terms:
                print(f"    undetected banned terms: {missing_terms}")
            if false_hits:
                print(f"    false positives on A1-exception corpus: {false_hits}")
            ok = False
        else:
            print("G-7 PASS")

    # G-8 (issue-59 WI-22, D-23 wording tripwire): zero hits for the retired
    # headline names across the seven sections; a fixture corpus proves the
    # detector fires on both phrases.
    violations = []
    for n in SECTIONS:
        if files[n].is_file():
            violations += d23_wording_violations(
                n, prose_body(files[n].read_text(encoding="utf-8"),
                              annex1=n == ANNEX1))
    fixture_hits = d23_wording_violations(
        "fixture", "The realistic annual take headlines the table; the "
                   "Realistic Revenue Band sits beside it.")
    hit_phrases = {v.rsplit(": ", 1)[1] for v in fixture_hits}
    if hit_phrases != {repr(p) for p in D23_BANNED}:
        violations.append(f"G-8 self-test: fixture hits {sorted(hit_phrases)}, "
                          f"expected one hit per banned phrase "
                          f"{sorted(repr(p) for p in D23_BANNED)}")
    ok = report("G-8", violations, "D-23 wording zero-hits") and ok

    # G-9 (issue-59 WI-21, D-28 scoping tripwire): banned global framing and
    # bare verdict heading over all seven sections; the Nagoya/
    # benefit-sharing sourcing co-occurrence rule over the three owning
    # sections. Negative fixture trips each clause; the positive fixture
    # (the final edited sentences) stays clean — G-7 self-test pattern.
    violations = []
    for n in SECTIONS:
        if not files[n].is_file():
            continue
        raw = files[n].read_text(encoding="utf-8")
        body = prose_body(raw, annex1=n == ANNEX1)
        if n in D28_SCOPE_SECTIONS:
            violations += d28_scoping_violations(n, raw, body)
        else:
            # framing + heading clauses everywhere; co-occurrence only in
            # the three sections that own the barrier wording.
            for v in d28_scoping_violations(n, raw, body):
                if "without the sourcing side" not in v:
                    violations.append(v)
    neg = "\n".join([
        "Nagoya compliance and biosecurity clearance shape every force.",
        "",
        "### Threat of new entrants: Low",
        "",
        "The Nagoya Protocol is an entry barrier competitors do not clear "
        "by default.",
    ])
    neg_hits = d28_scoping_violations("fixture", neg,
                                      prose_body(neg, annex1=False))
    for marker, label in (("banned framing", "the 'shape every force' clause"),
                          ("global verdict heading", "the bare heading clause"),
                          ("without the sourcing side", "the co-occurrence clause")):
        if not any(marker in v for v in neg_hits):
            violations.append(f"G-9 self-test: negative fixture missed {label}")
    if len([v for v in neg_hits if "without the sourcing side" in v]) != 2:
        violations.append("G-9 self-test: expected two co-occurrence trips in "
                          "the negative fixture (Nagoya + benefit-sharing "
                          "sentences), got "
                          f"{len([v for v in neg_hits if 'without the sourcing side' in v])}")
    pos = "\n".join([
        "Compliance load — access and benefit-sharing under the Nagoya "
        "Protocol, and biosecurity clearance — is the structural barrier on "
        "the physical sourcing stage: it weighs on keeping animal colonies "
        "and collecting venom, not on synthesis from sequence data, "
        "brokering, matching or training.",
        "",
        "Nagoya compliance and biosecurity clearance weigh on one stage of "
        "the chain, not every force: they are legal requirements on physical "
        "sourcing — access to genetic resources and the organisms that "
        "produce them — and they do not restrict the lines that synthesise "
        "from sequence data, broker, match or train.",
        "",
        "### Threat of new entrants: Low on sourcing, higher on the service lines",
        "",
        "Entry to the physical sourcing stage needs legally compliant access "
        "to animal genetic resources under the Nagoya Protocol, tracked "
        "through the Access and Benefit-Sharing Clearing-House.",
        "",
        "Whether venom proteomes and sequence data drawn from sourced "
        "animals trigger benefit-sharing obligations is an open legal "
        "question on the sourcing side.",
        "",
        "Navigating access and benefit-sharing obligations under the Nagoya "
        "Protocol for the network's sourcing partners is a competence "
        "competitors do not default to; it is a sourcing-side service, not "
        "an entry barrier protecting the broker, matching or training lines.",
    ])
    pos_hits = d28_scoping_violations("positive-fixture", pos,
                                      prose_body(pos, annex1=False))
    if pos_hits:
        violations.append(f"G-9 self-test: positive fixture tripped the "
                          f"detector: {pos_hits}")
    ok = report("G-9", violations, "D-28 scoping detector") and ok

    # G-INT: the real seven sections together — embeds unique per figure.
    seen: dict[str, str] = {}
    dupes = []
    for n in SECTIONS:
        if not files[n].is_file():
            continue
        for m in IMAGE_RE.finditer(files[n].read_text(encoding="utf-8")):
            target = m.group(0)
            if target in seen:
                dupes.append(f"{target} embedded in {seen[target]} and {n}")
            seen[target] = n
    ok = report("G-INT", dupes, "each figure embedded once across the report") and ok

    print("all gates PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
