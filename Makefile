PYTHON ?= python3
PYTHON_READONLY ?= PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTHON)
AAMOS_SOURCE_DIR ?=
AAMOS_RUN_ID ?= aamos-submission-R4
AAMOS_RUN_DIR ?= results/processed/aamos/$(AAMOS_RUN_ID)
OUTPUT_DIR ?=
REPORT ?=
RELEASE_DIR ?=
RUN_DIR ?=
SNAPSHOT ?=
TEST_REPORT ?=
FIGURE_DIR ?=

.PHONY: install test-python test-node test benchmark conformance component figures aamos aamos-figure verify verify-shell verify-public verify-tree reproduce-figures release-test-report seal-release

install:
	$(PYTHON) -m pip install -r requirements-lock.txt
	npm --prefix fabric/chaincode ci --ignore-scripts --no-audit --no-fund
	npm --prefix fabric/client ci --ignore-scripts --no-audit --no-fund

test-python:
	$(PYTHON_READONLY) -m unittest discover -s tests -p 'test_*.py' -v

test-node:
	npm --prefix fabric/chaincode test
	npm --prefix fabric/client test

test: test-python test-node

benchmark:
	$(PYTHON_READONLY) scripts/run_python_benchmarks.py --profile submission

conformance:
	$(PYTHON_READONLY) scripts/run_conformance.py --repetitions 200
	$(PYTHON_READONLY) scripts/run_component_conformance.py --repetitions 200

figures:
	$(PYTHON_READONLY) scripts/make_figures.py --figure python --mode submission
	$(PYTHON_READONLY) scripts/make_figures.py --figure component --mode submission
	$(PYTHON_READONLY) scripts/make_figures.py --figure window --mode submission

aamos:
	test -n "$(AAMOS_SOURCE_DIR)"
	$(PYTHON_READONLY) scripts/run_aamos_standard_enhanced.py \
		--source-dir "$(AAMOS_SOURCE_DIR)" \
		--profile submission \
		--bootstrap-reps 2000 \
		--bootstrap-seed 20260722 \
		--run-id "$(AAMOS_RUN_ID)"

aamos-figure:
	$(PYTHON_READONLY) scripts/make_figures.py \
		--figure aamos \
		--mode submission \
		--aamos-run "$(AAMOS_RUN_DIR)" \
		--output results/figures/submission

verify-shell:
	bash -n fabric/network/bootstrap.sh
	bash -n fabric/network/run_experiments.sh
	bash -n fabric/network/teardown.sh

verify-public:
	$(PYTHON_READONLY) scripts/verify_public_release.py \
		--manifest "public_release_manifest.json" \
		--project-root "."

verify-tree:
	$(PYTHON_READONLY) scripts/verify_tree_manifest.py --project-root "."

verify: test-python test-node verify-shell verify-public verify-tree

reproduce-figures:
	test -n "$(OUTPUT_DIR)"
	$(PYTHON_READONLY) scripts/make_figures.py --figure python \
		--mode submission --output "$(OUTPUT_DIR)"
	$(PYTHON_READONLY) scripts/make_figures.py --figure component \
		--mode submission --output "$(OUTPUT_DIR)"
	$(PYTHON_READONLY) scripts/make_figures.py --figure window \
		--mode submission --output "$(OUTPUT_DIR)"

release-test-report:
	test -n "$(REPORT)"
	$(PYTHON_READONLY) scripts/run_release_tests.py --output "$(REPORT)"

seal-release:
	test -n "$(RELEASE_DIR)"
	test -n "$(RUN_DIR)"
	test -n "$(SNAPSHOT)"
	test -n "$(TEST_REPORT)"
	test -n "$(FIGURE_DIR)"
	$(PYTHON_READONLY) scripts/build_public_release.py \
		--project-root "." \
		--release-dir "$(RELEASE_DIR)" \
		--run-dir "$(RUN_DIR)" \
		--snapshot "$(SNAPSHOT)" \
		--figure-dir "$(FIGURE_DIR)" \
		--test-report "$(TEST_REPORT)"
