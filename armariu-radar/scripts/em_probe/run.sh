set -u
mkdir -p em_probe_out
pip install --quiet openpyxl
python scripts/em_probe/test3.py em_probe_out 2>&1 | tee em_probe_out/test3_log.txt
