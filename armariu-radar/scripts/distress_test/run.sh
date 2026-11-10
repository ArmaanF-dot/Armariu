set -u
mkdir -p distress_test_out
python -m radar.dbsync pull --backfill || echo "no backfill db"
python scripts/distress_test/fetch.py ${STEPS:-} 2>&1 | tee -a distress_test_out/fetch_log.txt
