# Customer

Venom research has money and it is scattered. Five of the six packages have
identifiable payers; the sixth (P3) sells to almost every segment at once. The
binding constraint is not willingness but reachability: the network reaches
academic labs well, biotech partially, and the AI-bio frontier only through
public output.

## Segment ↔ package map

The package list assigns each package its segments; the map below is the
exact inverse — no package serves a segment absent from its entry, and no
segment is dropped. Where several packages serve one segment, a **primary**
is designated: the package whose payer description most directly matches that
segment's buying behaviour, with sizing depth as the tie-break. The
designation is an analyst judgement; each segment subsection restates the
rationale.

| Segment | Primary package | Other served packages |
| --- | --- | --- |
| Academic labs | P5 VenoMate | P3 Marketplace, P4 Peptide Service, P1 VENOSPATIAL, P6 G.E.M. |
| Pharma & biotech | P4 Peptide Service | P5 VenoMate, P3 Marketplace, P1 VENOSPATIAL, P6 G.E.M. |
| AI-bio frontier | P3 Marketplace | P5 VenoMate, P1 VENOSPATIAL |
| Investors & technology-transfer offices | P3 Marketplace | none assigned |
| Early-career researchers | P2 In Silico Workshop | none assigned |
| Future spin-offs | P2 In Silico Workshop | none assigned |

## Academic labs

**Key needs.** Credible access to venom and toxin research tooling and
in-silico capability without building it in-house: curated proteomics
workflows, toxin pharmacology data, outsourced bioinformatics, and routine
research services a grant-funded wet lab cannot staff.

**Who pays.** Grants. Labs buy tools and services out of grant budgets; the
assumptions file anchors lab-level external spend and institute-level service
outlays (bands in the Market section).

**Reachability.** High. The evidence is overwhelmingly academic output: the
venom-peptide corpus holds 412 works since 2015 from 509 institutions
(OpenAlex, pulled 5 Oct 2026), and the named network surfaces behind the
assumptions — ISCB membership and conference communities [@iscb2025;
@ismb2025], Vienna BioCenter core-facility user groups [@vbcf2024] — are
academic-first channels.

**Served packages.** Primary P5 VenoMate: its population is exactly this
community. Secondary P3 Marketplace (labs wanting AI capability without AI
teams), P4 Peptide Service (routine ordering), P1 VENOSPATIAL (institute
throughput), P6 G.E.M. (screening).

## Pharma & biotech

**Key needs.** Faster programme execution: custom peptides for antibody,
immunology and assay work; outsourced AI capacity without headcount;
multi-omics throughput; specialised venomics for the drug-lead subset.

**Who pays.** R&D budget. Purchasing is programme-driven and less
price-sensitive than academic spend when a service de-risks a milestone; the
assumptions file documents the engagement patterns behind that claim.

**Reachability.** Moderate. Biotech-heavy activity sits in the peptide and
expertise trial records (216 and 387 studies respectively, CT.gov, pulled
5 Oct 2026) — ecosystem context only, never evidence of commercial demand,
and the records overlap completely: all 216 chemistry studies also appear in
the 387 expertise studies, so the counts must not be summed. No
business-development contact inventory exists for this segment; direct
outreach is an open item.

**Served packages.** Primary P4 Peptide Service: its population is defined as
labs ordering custom peptides for research. Secondary P5 VenoMate, P3
Marketplace, P1 VENOSPATIAL, P6 G.E.M.

## AI-bio frontier

**Key needs.** Domain data and wet-lab credibility: curated venom and toxin
datasets and assays, expert-in-the-loop annotation, compute-plus-expertise
hybrids generalist providers cannot supply.

**Who pays.** R&D budget and VC-funded compute and expertise lines. Capacity
is real but episodic, tied to funding rounds.

**Reachability.** Low on recorded evidence. The population overlaps P3's
adopter-labs description, but the public target register carries
category-level rows only; no named targets are recorded in a public file.
Reachability rests on the OpenAlex adopter corpus and is directional.

**Served packages.** Primary P3 Marketplace. Secondary P5 VenoMate (domain
datasets and assays), P1 VENOSPATIAL.

## Investors & technology-transfer offices

**Key needs.** Diligence signals for venom, toxin and AI-bio theses:
independent evidence that the network, the data assets and the advisory bench
are real — publication corpora, funded-project footprints, trial activity.

**Who pays.** They buy due diligence. P3 lists due diligence for investors,
VC funds and technology-transfer offices as a service line; this replaces the earlier reading that treated the segment as a credibility
lever only. They do not buy the other packages.

**Reachability.** Moderate. Standard venture channels, advisory-network
introductions and circulation of this report reach the segment; the evidence
above is exactly the material that earns the diligence request. No investor
contact inventory exists in the segment inputs (open item).

**Served packages.** P3 Marketplace only, per the assignment.

## Early-career researchers

**Key needs.** Marketable specialised skills: venom and toxin proteomics
practice, AI-for-bioinformatics fluency, hands-on multi-omics training.

**Who pays.** Training fees, personal or institutional, routed through P2 In
Silico Workshop. Price evidence is recent: nineteen dated course comparables
(Annex 1) plus the EMBO/EMBL anchors behind the assumptions [@embo2026;
@embl2026].

**Reachability.** High. The same network surfaces aggregate this population,
and the comparables show an existing training market those channels feed.

**Served packages.** P2 In Silico Workshop only. Adjacent interest in
research tooling is a future re-mapping decision, not a current claim.

## Future spin-offs

**Key needs.** Founding capability: the same training plus the credibility,
playbooks and network access a lab spin-out lacks on day one.

**Who pays.** Near-term payment mirrors the training route. Venture-scale
budgets arrive after formation, when these entities re-enter the model as
AI-bio frontier or pharma buyers — a future re-mapping decision, not a
current revenue claim.

**Reachability.** Low today. The population is prospective; no spin-off
inventory exists, and the channel matures as early-career researchers
progress, indirectly through the P2 community.

**Served packages.** P2 In Silico Workshop only.

## Prioritisation matrix

Scores are 1–5 per dimension; the weighted composite is
`0.4 × value + 0.3 × reachability + 0.3 × willingness to pay`. **Scores are
proposed, pending segment-input confirmation**: they are analyst judgements
traceable to the cited sources, and the thin inventories for the frontier and
training segments carry the largest uncertainty. Investors and
technology-transfer offices are scored as a paying segment (willingness 3,
not 1) because P3 lists due diligence for them. The matrix
ranks segments, not packages.

| Segment | Value | Reachability | Willingness to pay | Weighted | Rank | Served package(s) |
| --- | --- | --- | --- | --- | --- | --- |
| Academic labs | 5 | 4 | 4 | 4.4 | 1 | P5 (primary); P3, P4, P1, P6 |
| Pharma & biotech | 4 | 3 | 5 | 4.0 | 2 | P4 (primary); P5, P3, P1, P6 |
| AI-bio frontier | 4 | 2 | 3 | 3.1 | 3 | P3 (primary); P5, P1 |
| Investors & TTOs | 3 | 3 | 3 | 3.0 | 4 | P3 |
| Early-career researchers | 2 | 4 | 2 | 2.6 | 5 | P2 |
| Future spin-offs | 2 | 2 | 2 | 2.0 | 6 | P2 |

Row rationales:

- Academic labs, highest value: five of six packages serve it; the two
  population anchors in the assumptions are built on this population.
- Pharma & biotech, highest willingness to pay: programme-driven R&D budget;
  offset by moderate reachability (no BD inventory).
- AI-bio frontier, strategic value, gated reachability: category-level
  register only, episodic funding-round payment.
- Investors & TTOs, now a paying line: due-diligence purchases via P3;
  willingness scored 3 as an analyst value, pending confirmation.
- Early-career researchers, reachable but low per-learner budgets; the
  comparables are recent and untested against buyer behaviour.
- Future spin-offs, downstream channel: all three dimensions are nascent;
  the segment compounds as training participants form ventures.

## Open items

- The AI-bio frontier register is category-level in public form, so
  frontier reachability stays directional.
- No investor or spin-off contact inventory exists; reachability for both is
  channel-based judgement, flagged above.
- The matrix scores are proposed; segment-input confirmation (Annex 2)
  precedes any final ranking.
- The willingness-to-pay score of 3 for investors and TTOs is an analyst
  value proposed with the re-scoring itself; the due-diligence interviews
  should confirm or correct it.
