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
	batch scale

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
SIZES    ?= 5x5,6x6,8x8
SCALE_OUT ?= ./scale_results

batch: ## Repeated seeded OSPF+SDN runs with mean/stddev CSVs (REPS=5)
	$(PYTHON) experiments/compare_batch.py --reps $(REPS) \
		--profile full --out-dir $(BATCH_OUT)

scale: ## Batches across constellation sizes (SIZES=5x5,6x6,8x8 REPS=5)
	$(PYTHON) experiments/compare_batch.py --reps $(REPS) \
		--profile full --sizes $(SIZES) --out-dir $(SCALE_OUT)

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean-artifacts: ## Remove generated experiment artifact directories
	rm -rf $(ARTIFACT_ROOT)-ospf-full $(ARTIFACT_ROOT)-sdn-full \
		$(ARTIFACT_ROOT)-ospf-basic $(ARTIFACT_ROOT)-sdn-basic \
		$(ARTIFACT_ROOT)-ospf-full-r* $(ARTIFACT_ROOT)-sdn-full-r* \
		$(ARTIFACT_ROOT)-ospf-basic-r* $(ARTIFACT_ROOT)-sdn-basic-r* \
		./starlink-*-grid-LeastDelay-* \
		$(ARTIFACT_ROOT) $(BATCH_OUT) $(SCALE_OUT) .config_scaled_*.json

clean: clean-artifacts ## Remove build/pytest cache and experiment dirs
	rm -rf build dist *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
