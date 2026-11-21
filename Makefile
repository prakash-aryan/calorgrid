PY := .venv/bin/python

.PHONY: all setup data bench year demo test quick profile-check clean

all: data bench year

setup:
	uv venv -p 3.12 .venv
	uv pip install -p .venv -r requirements.txt
	$(PY) -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/all-MiniLM-L6-v2')"
	$(PY) -c "import torchvision as t; t.models.resnet50(weights=t.models.ResNet50_Weights.IMAGENET1K_V2)"

data:            ## weather, ElCom tariff, MLPerf, Alibaba trace, workload datasets
	$(PY) scripts/fetch_data.py
	$(PY) scripts/fetch_workloads.py

bench:           ## measure runtime + energy of the real jobs on this machine (~10 min, keep machine idle)
	$(PY) scripts/benchmark_jobs.py

year:            ## 8,760 h x 4 policies + sweeps -> results/results.json
	$(PY) scripts/run_year.py
	$(PY) scripts/make_report.py

quick:
	$(PY) scripts/run_year.py --quick

profile-check:      ## compare case heat demand with supplied residential profiles
	$(PY) scripts/compare_heat_profiles.py

test:            ## energy balance + checkpoint/resume + scheduler invariants
	$(PY) -m pytest -q tests

demo:            ## dashboard + live controller
	$(PY) web/server.py 8050

clean:
	rm -rf results /tmp/heatshift_live /tmp/heatshift_bench
