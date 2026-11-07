set -u
mkdir -p em_probe_out
python scripts/em_probe/deb_probe.py em_probe_out 2>&1 | tee em_probe_out/deb_probe_log.txt
