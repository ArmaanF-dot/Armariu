set -u
mkdir -p contracts_test_out
python scripts/contracts_test/probe.py > contracts_test_out/probe.txt 2>&1
