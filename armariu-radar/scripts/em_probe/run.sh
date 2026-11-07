set -u
mkdir -p em_probe_out
pip install --quiet scikit-learn openpyxl
python scripts/em_probe/co3_sample.py em_probe_out 2>&1 | tee em_probe_out/co3_sample_log.txt
