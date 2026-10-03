# PERCH — developer entry points
.PHONY: help up down logs demo-good demo-bad demo-all test unit e2e fmt lint plots baseline clean

COMPOSE := docker compose
PYTHON ?= python

help:
	@echo "  up          Build and start the stack (no load generator)"
	@echo "  down        Stop the stack and remove volumes"
	@echo "  logs        Tail controller logs"
	@echo "  demo-good   Good canary: ramps to 100% unattended"
	@echo "  demo-bad    Broken canary: rolls back to 0% unattended"
	@echo "  unit        Decision/ramp tests (no Docker)"
	@echo "  e2e         HTTP tests against a running stack"
	@echo "  test        unit + e2e"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f controller

demo-good:
	$(PYTHON) scenarios/run.py good

demo-bad:
	$(PYTHON) scenarios/run.py bad-error

demo-all:
	$(PYTHON) scenarios/run.py all

test: unit e2e

unit:
	$(PYTHON) -m pytest tests/test_decision.py tests/test_ramp.py tests/test_proxy_writer.py -v

e2e:
	$(PYTHON) -m pytest tests/test_e2e.py -v

baseline:
	$(PYTHON) analysis/compare_baseline_vs_detector.py

plots:
	$(PYTHON) analysis/plot_results.py

fmt:
	$(PYTHON) -m black .

lint:
	$(PYTHON) -m ruff check .

clean:
	$(PYTHON) -c "import pathlib; p=pathlib.Path('results'); \
[f.unlink() for f in p.rglob('*') if f.is_file() and f.name!='.gitkeep']"
