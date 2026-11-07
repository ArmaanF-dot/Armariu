set -u
mkdir -p em_probe_out
pip install --quiet openpyxl
python scripts/em_probe/inspect_files.py em_probe_out 2>&1 | tee em_probe_out/inspect_log.txt
