import csv, numpy as np
def load(path):
    rows = list(csv.DictReader(open(path))); cols = {}
    for k in rows[0]:
        v = [r[k] for r in rows]
        try: cols[k] = np.array([np.nan if x in ("", "None") else float(x) for x in v])
        except ValueError: cols[k] = np.array(v)
    return cols
