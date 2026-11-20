# PERCH — developer entry points
.PHONY: help up down logs demo-good demo-bad demo-all test unit e2e eval fmt lint plots baseline campaign simulate experiments figures clean

COMPOSE := docker compose
PYTHON ?= python
# Run folder for `make plots`; defaults to the live files of an unscripted session.
RUN ?= results

help:
	@echo "  up          Build and start the stack (no load generator)"
	@echo "  down        Stop the stack and remove volumes"
	@echo "  logs        Tail controller logs"
	@echo "  demo-good   Good canary: ramps to 100% unattended"
	@echo "  demo-bad    Broken canary: rolls back to 0% unattended"
	@echo "  unit        Decision/ramp/proxy-writer tests (no Docker)"
	@echo "  e2e         HTTP tests against a running stack (not during a scenario)"
	@echo "  eval        Evaluation tests: the detectors' claims, checked by simulation (~30s)"
	@echo "  test        unit + e2e"
	@echo "  simulate    1000 simulated rollouts per scenario and mode (no Docker, ~40s)"
	@echo "  experiments Simulation experiments E1-E4 -> results/experiments/simulation (~1.5 min)"
	@echo "  figures     Figures -> results/experiments/figures (CAMPAIGN=<dir> adds live ones)"
	@echo "  baseline    Naive vs statistical, good + bad canary, 5 live runs each (~1 hour)"
	@echo "  campaign    Every scenario x every mode, 5 live runs each (~2 hours)"
	@echo "  plots       Re-plot a run: make plots RUN=results/archive/<run>"
	@echo "  clean       Delete live scratch files in results/ (keeps results/archive/)"

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
	$(PYTHON) -m pytest tests/test_decision.py tests/test_stats.py tests/test_ramp.py tests/test_proxy_writer.py tests/test_campaign.py tests/test_runner.py -v

e2e:
	$(PYTHON) -m pytest tests/test_e2e.py -v

eval:
	$(PYTHON) -m pytest tests/test_evaluation.py -v

simulate:
	$(PYTHON) analysis/simulate_rollouts.py --runs 1000

baseline:
	$(PYTHON) analysis/compare_baseline_vs_detector.py --repeats 5

campaign:
	$(PYTHON) analysis/run_campaign.py --repeats 5

experiments:
	$(PYTHON) analysis/experiments.py

figures:
	$(PYTHON) analysis/plot_experiments.py $(if $(CAMPAIGN),--campaign $(CAMPAIGN))

plots:
	$(PYTHON) analysis/plot_results.py --log $(RUN)/decision_log.jsonl --out-dir $(RUN)

fmt:
	$(PYTHON) -m black .

lint:
	$(PYTHON) -m ruff check .

clean:
	$(PYTHON) -c "import pathlib; p=pathlib.Path('results'); \
[f.unlink() for f in p.glob('*') if f.is_file() and f.name!='.gitkeep']"
