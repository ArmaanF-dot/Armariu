set -u
mkdir -p em_probe_out
pip install --quiet scikit-learn
python scripts/em_probe/deb_analysis.py em_probe_out scripts/em_probe 2>&1 | tee em_probe_out/deb_analysis_log.txt
