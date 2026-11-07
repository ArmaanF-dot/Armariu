set -u
mkdir -p em_probe_out
pip install --quiet openpyxl scikit-learn
python scripts/em_probe/backtest_co2.py em_probe_out 2>&1 | tee em_probe_out/backtest2_log.txt
