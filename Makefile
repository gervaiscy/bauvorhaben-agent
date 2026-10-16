.DEFAULT_GOAL := help
PYTHON ?= python3
VENV := .venv
RUNPY := $(VENV)/bin/python
CASE ?= sample
export CASE CONTRACT BLB SIGNED SOURCE PAGE REVIEWER NOTE CONFIRM FINDING

.PHONY: help setup doctor auth-check new run fresh retry status open extract units reconcile review timing report test freeze
help:
	@echo 'Bauvo — local document pipeline'
	@echo '  make setup                 Install isolated Python dependencies'
	@echo '  make doctor                Check local dependencies/configuration'
	@echo '  make auth-check            Test a small live Gemini request'
	@echo '  make new CASE=name CONTRACT="/path/contract.pdf" BLB="/path/blb.pdf" SIGNED=YYYY-MM-DD'
	@echo '  make fresh-extract CASE=name   Extract into a new run; stop before working units'
	@echo '  make open-extraction CASE=name SOURCE=blb   Compare images, text and grouping'
	@echo '  make run CASE=name         Run/resume the entire pipeline'
	@echo '  make fresh CASE=name       Start a separate run; keep old results'
	@echo '  make status CASE=name      Show checkpoints and last failure'
	@echo '  make open CASE=name        Open the generated HTML draft'
	@echo '  make extract|units|reconcile|review|timing|report CASE=name'
	@echo '  make resolve-extraction CASE=name SOURCE=blb PAGE=64 REVIEWER=name NOTE=reason CONFIRM=reviewed'
	@echo '  make approve-extraction CASE=name SOURCE=blb REVIEWER=name NOTE=reason CONFIRM=reviewed'
	@echo '  make test                  Run offline tests (no API charges)'
setup:
	$(PYTHON) -m venv $(VENV)
	$(RUNPY) -m pip install -r requirements.lock

doctor auth-check new run fresh fresh-extract status open open-extraction resolve-extraction approve-extraction regroup extract units reconcile review timing report:
	$(RUNPY) scripts/bauvo.py $@
retry: run

test:
	$(RUNPY) pipeline/run_tests.py
	$(RUNPY) -m unittest discover -s tests -v
freeze:
	$(RUNPY) -m pip freeze > requirements.lock

.PHONY: resolve-extraction regroup
