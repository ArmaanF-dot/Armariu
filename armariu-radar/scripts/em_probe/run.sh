set -u
mkdir -p em_probe_out
python scripts/em_probe/br_refi.py em_probe_out 2>&1 | tee em_probe_out/br_refi_log.txt
