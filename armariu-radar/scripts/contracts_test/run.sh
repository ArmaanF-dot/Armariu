set -u
mkdir -p contracts_test_out
python -m radar.dbsync pull || echo "no radar db"
python scripts/contracts_test/fetch.py 2>&1 | tee -a contracts_test_out/fetch_log.txt
