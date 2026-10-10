# Methods — Annex 1

Evidence provenance behind the report: every data source, snapshot and sizing assumption, traced to files stored in this repository. All content compiles from committed files — nothing newly pulled or re-derived.

Data as of 2026-10-05: the retuned six-package pull. Compiled and revised 2026-10-05 per `decisions.md`.

## Evidence sources

Three automated sources with query strings in `config/queries.yaml`, plus manual evidence files under `data/manual/`:

| source | records pulled | role |
| --- | --- | --- |
| OpenAlex | scholarly publication records (5 Oct 2026) | driver corpus per package |
| ClinicalTrials.gov | trial registry entries (5 Oct 2026) | ecosystem context |
| CORDIS | EU project funding records (5 Oct 2026) | ecosystem context |
| Manual files | 7 registers (competitors, training offers, assumptions, etc.) | market sizing, pricing comparables, competitor inventory |

Query strings, snapshot files and manifests live in their respective pipeline locations: `config/queries.yaml`, `data/raw/*/`, and per-snapshot `*.manifest.json`. The manual-file schemas and vocabularies are enforced by `scripts/check_manual.py`.

## Sizing assumptions

All 27 rows of `data/manual/assumptions.csv` feed the simulation; the 8 test-flagged rows below are the Annex 2 interview set. Three layers: `context` (generic market reference, never a headline), `tam` (total view: population × spend rate), `som` (delivery ceiling: capacity × price). Every band is a low–mid–high range; point estimates are not allowed.

| id | package | layer | parameter | low | mid | high | unit | confidence | test_in_validation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | P1 VENOSPATIAL | som | seats_per_year | 100 | 225 | 350 | seats | low | y |
| 7 | P2 In Silico Workshop | som | seats_per_year | 32 | 40 | 48 | seats | low | y |
| 10 | P3 Marketplace | tam | active_labs | 3000 | 6000 | 8000 | labs | low | y |
| 12 | P3 Marketplace | som | engagements_per_year | 12 | 30 | 48 | engagements | low | y |
| 17 | P4 Peptide Service | som | syntheses_per_year | 24 | 36 | 48 | syntheses | low | y |
| 20 | P5 VenoMate | tam | active_labs | 200 | 550 | 1200 | labs | low | y |
| 22 | P5 VenoMate | som | paying_profiles | 8 | 13 | 20 | profiles | low | y |
| 26 | P6 G.E.M. | som | screens_per_year | 12 | 24 | 48 | screens | low | y |

The platform referral band (3–5% of routed work) lives in `data/manual/platform.csv`. Derived market bands are in `data/processed/market_sizing.csv`, rendered as `report/generated/market_sizing.md`. This annex quotes assumption inputs, never re-derives products.

These bands are analyst estimates, not yet validated. The Validation Plan (Annex 2) provides the mechanism to narrow these bands through structured interviews.

## Reproducibility

Pipeline: `make pull → build → size → figures → numbers → report`. Sizing uses a fixed seed, 10,000 draws, so re-runs reproduce every quantile from stored inputs. The snapshot layer is append-only — a dated file is never overwritten. `make check` runs every offline check (`scripts/check_*.py`) before any report edit.

## Open items

- Record counts move with every pull; the prose cites the 5 Oct 2026 pull and figures regenerate from the newest snapshot.
- P6 G.E.M. trial and funding counts are small (10 and 8 records); antivenom-programme interviews in Annex 2 are the qualitative mitigation.
