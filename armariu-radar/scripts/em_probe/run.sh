set -u
mkdir -p em_probe_out
pip install --quiet openpyxl scikit-learn
python scripts/em_probe/backtest_co.py em_probe_out 2>&1 | tee em_probe_out/backtest_log.txt
