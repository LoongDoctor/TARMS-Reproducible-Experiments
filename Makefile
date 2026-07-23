PYTHON ?= python3
RESULTS ?= results
RERUN_RESULTS ?= reproduced_results

.PHONY: install test-python test-node test data benchmark conformance component figures figures-rerun verify

install:
	$(PYTHON) -m pip install -r requirements-lock.txt
	npm --prefix fabric/chaincode ci --ignore-scripts --no-audit --no-fund
	npm --prefix fabric/client ci --ignore-scripts --no-audit --no-fund

test-python:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

test-node:
	npm --prefix fabric/chaincode test
	npm --prefix fabric/client test

test: test-python test-node

data:
	$(PYTHON) scripts/verify_run_manifests.py

benchmark:
	$(PYTHON) scripts/run_python_benchmarks.py --profile submission --output-root $(RERUN_RESULTS)

conformance:
	$(PYTHON) scripts/run_conformance.py --repetitions 200 --output-root $(RERUN_RESULTS)
	$(PYTHON) scripts/run_component_conformance.py --repetitions 200 --output-root $(RERUN_RESULTS)

figures:
	$(PYTHON) scripts/make_figures.py --figure python --mode submission --results $(RESULTS)
	$(PYTHON) scripts/make_figures.py --figure component --mode submission --results $(RESULTS)
	$(PYTHON) scripts/make_figures.py --figure window --mode submission --results $(RESULTS)

figures-rerun:
	$(PYTHON) scripts/make_figures.py --figure python --mode submission --results $(RERUN_RESULTS)
	$(PYTHON) scripts/make_figures.py --figure component --mode submission --results $(RERUN_RESULTS)
	$(PYTHON) scripts/make_figures.py --figure window --mode submission --results $(RERUN_RESULTS)

verify: test data
	bash -n fabric/network/bootstrap.sh
	bash -n fabric/network/run_experiments.sh
	bash -n fabric/network/teardown.sh
	$(MAKE) figures PYTHON=$(PYTHON) RESULTS=$(RESULTS)
