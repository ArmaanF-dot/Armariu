set -u
mkdir -p em_probe_out
pip install --quiet scikit-learn openpyxl
python scripts/em_probe/co5_list.py em_probe_out 2>&1 | tee em_probe_out/co5_list_log.txt
