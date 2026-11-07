set -u
mkdir -p em_probe_out
pip install --quiet scikit-learn
python scripts/em_probe/br_fre_wall.py em_probe_out scripts/em_probe 2>&1 | tee em_probe_out/br_fre_log.txt
