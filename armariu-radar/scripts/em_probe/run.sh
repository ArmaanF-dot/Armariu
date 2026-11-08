set -u
mkdir -p em_probe_out
python scripts/em_probe/co4_probe.py 2>&1 | tee em_probe_out/co4_probe_log.txt
