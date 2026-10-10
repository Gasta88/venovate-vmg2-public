# Validation Plan — Annex 2

Eight viability assumptions carry the report's numbers; each has an interview probe and test order. This annex gives the assumption set, category-level target list, and rules for folding results.

Data as of 2026-10-05 (retuned six-package pull). Every numeral below is a stored assumption row.

**These bands are analyst estimates with low confidence. Validation interviews are the ONLY mechanism to narrow these bands. Do not pre-narrow without data.**

## Assumptions to test

| id | assumption | package | layer | parameter | low | mid | high | unit | confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V1 | 3 | P1 VENOSPATIAL | som | seats_per_year | 100 | 225 | 350 | seats | low |
| V2 | 7 | P2 In Silico Workshop | som | seats_per_year | 32 | 40 | 48 | seats | low |
| V3 | 12 | P3 Marketplace | som | engagements_per_year | 12 | 30 | 48 | engagements | low |
| V4 | 17 | P4 Peptide Service | som | syntheses_per_year | 24 | 36 | 48 | syntheses | low |
| V5 | 22 | P5 VenoMate | som | paying_profiles | 8 | 13 | 20 | profiles | low |
| V6 | 26 | P6 G.E.M. | som | screens_per_year | 12 | 24 | 48 | screens | low |
| V7 | 10 | P3 Marketplace | tam | active_labs | 3000 | 6000 | 8000 | labs | low |
| V8 | 20 | P5 VenoMate | tam | active_labs | 200 | 550 | 1200 | labs | low |

All eight are analyst-set bands at low confidence. Each delivery ceiling (V1–V6) multiplies capacity by a stated price (V1/V4/V5/V6) or analyst-constructed band (V2/V3). The two flagged TAM populations (V7, V8) are the only TAM rows tested here.

What each assumption must survive:

- **V1 [LOW CONFIDENCE] — deliverable seat capacity.** Two 25–50-seat workshops plus one 50–250-seat training cohort per year, at current staffing, without new hires.
- **V2 [LOW CONFIDENCE] — cohort throughput.** Two cohorts per year of 16–24 seats at practical-course scale, schedulable with instructor availability.
- **V3 [LOW CONFIDENCE] — engagement throughput.** One to four brokered fee engagements a month across consultancy, advisory programmes, sponsored collaborations and grant-funded work must match actual conversion rates.
- **V4 [LOW CONFIDENCE] — throughput at tariff.** Two to four disulfide-rich syntheses a month from one research-assistant-scale line at €7,000–14,000 totals must survive pilot conversion from indicative to approved prices.
- **V5 [LOW CONFIDENCE] — paying-profile conversion.** Eight to twenty paying profiles a year must convert against package documentation's own year-2 plan of ten facilities plus three industry.
- **V6 [LOW CONFIDENCE] — screen throughput.** Twelve to forty-eight screens a year at 0.2–0.5 FTE allocation, five person-days per screen, must fit programme calendars.
- **V7 [LOW CONFIDENCE] — AI-adopter lab population.** The "wants computational or AI capability, no dedicated in-house AI team" boundary must be a category buyers recognise, without double-counting against V8.
- **V8 [LOW CONFIDENCE] — venom/toxin lab population.** The exclusion rule (no general toxicology, no clinical-only groups, active in last five years) must match what practitioners recognise as the working community.

## Interview structure

Seven probe blocks target the six buyer segments plus one shared industry block, each with a semi-structured script derived from the assumptions above. The table below summarises coverage:

| segment block | target count | assumptions exercised |
| --- | --- | --- |
| Venom/toxin research labs | 3 | V8, V5 |
| AI-adopter labs without AI teams | 3 | V7, V3 |
| Peptide-ordering labs | 2 | V4 |
| AI-bio frontier organisations | 2 | V3, V7 |
| Spatial-omics institute buyers | TBD | V1 |
| Training buyers | TBD | V2 |
| Antivenom and envenoming programmes | 1 | V6 |

Eleven named targets total; ten are low-reachability cold outreach, one is low-reachability introduction through package lead.

## Validation sequencing

Order by what the report leans on hardest: population probes (V7, V8) ride in first blocks alongside capacity probes; six capacity rows (V1–V6) spread across all waves. Blocks 1–3 run first (largest populations, clearest spend questions), Block 7 alongside where antivenom contacts respond, Block 4 as frontier confirmation pass, Blocks 5–6 on training and institute channels.

Cadence: two contacts per week per block, notes coded against V1–V8 as they arrive. Anything outstanding at 10 Oct 2026 report date ships as validation-plan commitment (this annex), not as results.

**Decision rule:** validation never changes a band directly. Revised cells flow **from the sizing assumptions through the generated tables into the Market section** — edit the assumption (with dated source text updated) and regenerate; every band, figure and number follows automatically. This annex states qualitative outcomes only; no revised numerals appear here. A row is unflagged only via the same stored edit; any change to the assumptions-to-test set needs a recorded owner decision (5–8 band stays). **Bands change ONLY through the assumptions → sizing → report flow after validation — never speculatively.**

## Open items

- V1 and V2 have no named targets; Blocks 5–6 run through course and core-facility contacts, not named interviews. Naming targets is the next execution step.
- Population rows anchored, not counted. V7 and V8 extrapolate from membership, conference and publication counts; interviews test category boundary, not a census.
- Small evidence cells. P6 G.E.M.'s trial and funding records are few (10 and 8); Block 7 is qualitative mitigation; no re-pull before report date.
- No interview results yet. Results fold in through assumptions → sizing → report flow via future revision, never as inline edits here.
- Platform referral band outside eight-row set. The 3–5% referral rate behind the Summary's platform headline is a stored manual row with its own written basis (package documentation), not one of the eight viability assumptions; its routed-work base is an analyst ceiling at low confidence, unconfirmed by package leads.
