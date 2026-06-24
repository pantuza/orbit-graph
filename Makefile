# StarryNet — local emulation and OSPF vs SDN comparison
#
# Run `make` or `make help` to list targets. Help text lives in ## comments on
# each target rule line.

.DEFAULT_GOAL := help

PYTHON       ?= python3
ARTIFACT_ROOT = ./starlink-5-5-550-53-grid-LeastDelay
DOCKER_IMAGE  = lwsen/starlab_node:1.0

.PHONY: help setup install-deps docker-pull test clean clean-artifacts \
	ospf sdn ospf_simple sdn_simple stats stats_simple compare compare_simple \
	batch scale plots paper-init paper-clean paper-plots \
	paper-5x5 paper-6x6 paper-7x7 paper-8x8 paper-9x9 paper-10x10

help: ## Show this help
	@echo "StarryNet Makefile targets:"
	@echo ""
	@grep -E '^[a-zA-Z0-9_.-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}' | sort

# ---------------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------------

setup: install-deps docker-pull ## Install Python deps (editable) and pull Docker image

install-deps: ## pip install requirements + package (editable)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r tools/requirements.txt
	$(PYTHON) -m pip install -e .

docker-pull: ## Pull StarryNet container image lwsen/starlab_node:1.0 (~300 MB)
	docker pull $(DOCKER_IMAGE)

test: ## Run SDN routing unit tests
	$(PYTHON) -m pytest starrynet/sdn_routing/test_routing_unit.py -q

# ---------------------------------------------------------------------------
# OSPF vs SDN experiments → $(ARTIFACT_ROOT)-{ospf,sdn}-{full,basic}/
# ---------------------------------------------------------------------------

ospf: ## Full OSPF run (damage, recovery, pings @ t=40/50) → …-ospf-full/
	$(PYTHON) experiments/compare_single_run.py --mode ospf --profile full

sdn: ## Full SDN run (damage, recovery, pings @ t=40/50) → …-sdn-full/
	$(PYTHON) experiments/compare_single_run.py --mode sdn --profile full

ospf_simple: ## Basic OSPF run (no damage, early pings) → …-ospf-basic/
	$(PYTHON) experiments/compare_single_run.py --mode ospf --profile basic

sdn_simple: ## Basic SDN run (no damage, early pings) → …-sdn-basic/
	$(PYTHON) experiments/compare_single_run.py --mode sdn --profile basic

stats: ## Summarize ping + metrics from full OSPF and SDN artifact dirs
	$(PYTHON) experiments/compare_summarize.py \
		$(ARTIFACT_ROOT)-ospf-full \
		$(ARTIFACT_ROOT)-sdn-full

stats_simple: ## Summarize basic-profile OSPF and SDN runs
	$(PYTHON) experiments/compare_summarize.py \
		$(ARTIFACT_ROOT)-ospf-basic \
		$(ARTIFACT_ROOT)-sdn-basic

compare: ospf sdn stats ## Run full OSPF + SDN experiments, then print stats

compare_simple: ospf_simple sdn_simple stats_simple ## Run basic OSPF + SDN, then print stats

# ---------------------------------------------------------------------------
# Statistical batches → $(BATCH_OUT)/*.csv
# ---------------------------------------------------------------------------

REPS     ?= 5
BATCH_OUT ?= ./batch_results
SCALE_OUT ?= ./scale_results
SIMULATION ?= simulation.json
RESULTS   ?= $(SCALE_OUT)
FIGDIR    ?= ./figures

batch: ## Repeated seeded OSPF+SDN runs with mean/stddev CSVs (REPS=5)
	$(PYTHON) experiments/compare_batch.py --reps $(REPS) \
		--profile full --out-dir $(BATCH_OUT)

scale: ## Scale sweep from simulation.json (SCALE_REPS= overrides plan reps)
	$(PYTHON) experiments/compare_batch.py --simulation $(SIMULATION) \
		$(if $(SCALE_REPS),--reps $(SCALE_REPS),)

plots: ## Journal figures from batch/scale CSVs (RESULTS=./scale_results)
	$(PYTHON) experiments/plot_results.py --in-dir $(RESULTS) --out-dir $(FIGDIR)

# ---------------------------------------------------------------------------
# Paper scale sweep → ./scale_results_paper/*.csv (one grid per make target)
# ---------------------------------------------------------------------------

PAPER_OUT  ?= ./scale_results_paper
PAPER_REPS ?= 10

paper-init: ## Create paper results directory
	mkdir -p $(PAPER_OUT)

paper-clean: ## Remove paper CSVs (keeps README)
	rm -f $(PAPER_OUT)/*.csv

paper-plots: ## Figures from merged paper CSVs (PAPER_OUT=./scale_results_paper)
	$(PYTHON) experiments/plot_results.py --in-dir $(PAPER_OUT) --out-dir $(FIGDIR)

paper-5x5: paper-init ## 5×5 × PAPER_REPS (default 10); fresh CSVs
	$(PYTHON) experiments/compare_batch.py --reps $(PAPER_REPS) \
		--sizes 5x5 --durations 5x5=100 --out-dir $(PAPER_OUT)

paper-6x6: paper-init ## 6×6 × PAPER_REPS; append to scale_results_paper
	$(PYTHON) experiments/compare_batch.py --reps $(PAPER_REPS) \
		--sizes 6x6 --durations 6x6=120 --out-dir $(PAPER_OUT) --append

paper-7x7: paper-init ## 7×7 × PAPER_REPS; append
	$(PYTHON) experiments/compare_batch.py --reps $(PAPER_REPS) \
		--sizes 7x7 --durations 7x7=250 --out-dir $(PAPER_OUT) --append

paper-8x8: paper-init ## 8×8 × PAPER_REPS; append (400s — room for 3 handovers)
	$(PYTHON) experiments/compare_batch.py --reps $(PAPER_REPS) \
		--sizes 8x8 --durations 8x8=400 --out-dir $(PAPER_OUT) --append

paper-9x9: paper-init ## 9×9 × PAPER_REPS; append
	$(PYTHON) experiments/compare_batch.py --reps $(PAPER_REPS) \
		--sizes 9x9 --durations 9x9=350 --out-dir $(PAPER_OUT) --append

paper-10x10: paper-init ## 10×10 × PAPER_REPS; append
	$(PYTHON) experiments/compare_batch.py --reps $(PAPER_REPS) \
		--sizes 10x10 --durations 10x10=400 --out-dir $(PAPER_OUT) --append

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean-artifacts: ## Remove generated experiment artifact directories
	rm -rf $(ARTIFACT_ROOT)-ospf-full $(ARTIFACT_ROOT)-sdn-full \
		$(ARTIFACT_ROOT)-ospf-basic $(ARTIFACT_ROOT)-sdn-basic \
		$(ARTIFACT_ROOT)-ospf-full-r* $(ARTIFACT_ROOT)-sdn-full-r* \
		$(ARTIFACT_ROOT)-ospf-basic-r* $(ARTIFACT_ROOT)-sdn-basic-r* \
		./starlink-*-grid-LeastDelay-* \
		$(ARTIFACT_ROOT) $(BATCH_OUT) $(SCALE_OUT) $(PAPER_OUT)/*.csv \
		.config_scaled_*.json

clean: clean-artifacts ## Remove build/pytest cache and experiment dirs
	rm -rf build dist *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
