set -u
mkdir -p em_probe_out
pip install --quiet scikit-learn openpyxl
python scripts/em_probe/co4_test.py em_probe_out 2>&1 | tee em_probe_out/co4_test_log.txt
