set -u
mkdir -p em_probe_out
: python scripts/em_probe/probe.py em_probe_out 2>&1 | tee em_probe_out/probe_log.txt
python scripts/em_probe/test2.py em_probe_out 2>&1 | tee em_probe_out/test2_log.txt
