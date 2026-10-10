# VENOVATE VMG2

Thin appendix generator for the VMG2 business plan analysis. Desk research and
writing first; scripts only feed market sizing and a few trend charts. No
Docker, no CI, no database, no orchestration — plain Python scripts + a Makefile.

[![DOI](https://zenodo.org/badge/DOI/YOUR.DOI.HERE.svg)](https://doi.org/YOUR.DOI.HERE)

## Rerun everything (3 commands)

```sh
make setup     # create .venv and install deps (uv sync)
make pull      # re-pull raw snapshots from each source
make all       # build tables → sizes → figures → report
```

## Status

- Data as of: 2026-10-05 (retuned six-package pull)
- Report: 10 Oct 2026

## Provenance

The report's evidence comes from three automated sources and a manual layer,
all committed here:

- **OpenAlex** (scholarly records), **ClinicalTrials.gov** (trial registry)
  and **CORDIS** (EU project funding) are pulled per package with the query
  strings in `config/queries.yaml` into dated snapshot directories under
  `data/raw/<source>/<YYYY-MM-DD>/`; every snapshot carries a manifest with
  the exact query, request URL (credentials redacted), UTC time, record
  count and integrity hash. The current pull is 2026-10-05; the 2026-09-28
  snapshot is kept for audit only and the report does not read it.
- **Manual evidence** (competitor inventory, course comparables, target
  categories, sizing assumptions) lives in `data/manual/`, each row with its
  source URL and confidence.
- Snapshots are matched into `data/processed/` tables, sized into
  `data/processed/market_sizing.csv`, rendered into `report/figures/` and
  `report/generated/`, and every monetary figure the report displays is
  substituted from `report/generated/numbers.yaml` at build time.

Evidence provenance in full — query strings, snapshot inventory, assumption
rows and record counts — is documented in the report's Methods annex
(`report/A1_methods.md`).

## Layout

- `config/` — single source of truth: per-source search terms (`queries.yaml`),
  packages/segments mapping (`packages.yaml`)
- `scripts/` — pull → ingest → build → size → figures pipeline
- `data/raw/` — committed snapshots + `manifest.json` per pull
- `data/manual/` — hand-curated CSVs, committed
- `data/processed/` — small derived CSVs, committed so the report builds offline
- `report/` — Markdown sections → `.docx` via pandoc; scripts only write to
  `generated/` and `figures/`
- `build/` — gitignored drafts (e.g. `vmg2_draft.docx`)

## Biosecurity

Toxin-specific target lists are kept at category level only (e.g. "AI-first
drug-discovery lab", not named organisations). This repository contains no
named interview targets or internal priority lists.
