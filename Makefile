UV := uv
PY  := $(UV) run python

.PHONY: setup pull build size figures numbers report report-pdf pdf-annex-a1 pdf-annex-a2 all check

## setup: create .venv and install deps
setup:
	$(UV) sync

## pull: re-pull raw snapshots from every source
pull:
	$(PY) scripts/pull_openalex.py
	$(PY) scripts/pull_ctgov.py
	$(PY) scripts/pull_cordis.py

## build: raw -> processed tables
build:
	$(PY) scripts/build_tables.py

## size: assumptions.csv -> low/mid/high per package
size:
	$(PY) scripts/size_markets.py

## figures: processed -> report/figures/*.png + report/generated/*.md
figures:
	$(PY) scripts/make_figures.py

## numbers: market_sizing.csv -> report/generated/numbers.yaml (D-11 placeholders)
numbers:
	$(PY) scripts/make_numbers.py

## report: pandoc sections -> build/vmg2_draft.docx
## Flag order (issue-53): pandoc applies citeproc and lua filters in
## command-line order — the numbers filter runs BEFORE --citeproc so it
## substitutes placeholders on the raw Str nodes (spec Risk row 5).
REPORT_SRC := report/00_summary.md report/01_customer.md report/02_market.md \
              report/03_industry.md report/04_competitive.md report/A1_methods.md \
              report/A2_validation_plan.md

report:
	@for f in $(REPORT_SRC) report/reference.docx report/references.yaml \
	  report/filters/numbers.lua report/generated/numbers.yaml; do \
	  [ -s "$$f" ] || { echo "ERROR: missing or empty report input: $$f" >&2; exit 1; }; \
	done
	mkdir -p build
	pandoc $(REPORT_SRC) --reference-doc=report/reference.docx \
	  --resource-path=.:report \
	  --lua-filter=report/filters/numbers.lua \
	  --citeproc --bibliography=report/references.yaml \
	  --metadata=reference-section-title=References \
	  -o build/vmg2_draft.docx

## report-pdf: pandoc sections -> build/vmg2_draft.pdf
## Requires xelatex or pdflatex (install via TinyTeX: pandoc --pdf-engine=... install-tinytex)
## Flag order (issue-53) still holds: numbers.lua runs BEFORE --citeproc.
## table_landscape.lua turns tables of 6+ columns onto landscape pages so
## their cells fit at a readable font size instead of overprinting each
## other; it is LaTeX-only, so the docx target needs no equivalent.
PDF_ENGINE ?= xelatex

report-pdf:
	@for f in $(REPORT_SRC) report/references.yaml \
	  report/filters/numbers.lua report/filters/table_landscape.lua \
	  report/generated/numbers.yaml; do \
	  [ -s "$$f" ] || { echo "ERROR: missing or empty report input: $$f" >&2; exit 1; }; \
	done
	mkdir -p build
	pandoc $(REPORT_SRC) \
	  --resource-path=.:report \
	  --lua-filter=report/filters/numbers.lua \
	  --lua-filter=report/filters/table_landscape.lua \
	  --citeproc --bibliography=report/references.yaml \
	  --metadata=reference-section-title=References \
	  --pdf-engine=$(PDF_ENGINE) \
	  -H report/filters/header.tex \
	  -V fontsize=11pt \
	  -o build/vmg2_draft.pdf

## pdf-annex-a1: build annex A1 as standalone PDF
pdf-annex-a1:
	@[ -s report/A1_methods.md ] || { echo "ERROR: missing report/A1_methods.md" >&2; exit 1; }
	mkdir -p build
	pandoc report/A1_methods.md \
	  --standalone \
	  --pdf-engine=$(PDF_ENGINE) \
	  --resource-path=.:report \
	  --lua-filter=report/filters/table_landscape.lua \
	  -H report/filters/header.tex \
	  -V fontsize=11pt \
	  -o build/annex_a1_methods.pdf

## pdf-annex-a2: build annex A2 as standalone PDF
pdf-annex-a2:
	@[ -s report/A2_validation_plan.md ] || { echo "ERROR: missing report/A2_validation_plan.md" >&2; exit 1; }
	mkdir -p build
	pandoc report/A2_validation_plan.md \
	  --standalone \
	  --pdf-engine=$(PDF_ENGINE) \
	  --resource-path=.:report \
	  --lua-filter=report/filters/table_landscape.lua \
	  -H report/filters/header.tex \
	  -V fontsize=11pt \
	  -o build/annex_a2_validation.pdf

## all: full pipeline
all: pull build size figures numbers report

GATES := $(wildcard scripts/check_*.py)

## check: run every offline verification gate (CI substitute — repo has no CI by ruling;
## deliberately NOT chained into `all`: gates stay runnable offline on a fresh clone)
check:
	@for gate in $(GATES); do echo "== $$gate"; $(PY) $$gate || exit 1; done