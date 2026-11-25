"""Inspect only partition names and sample schemas in the existing Hive catalogue."""
import json
from pathlib import Path
import sys

import pyarrow.parquet as pq

cfg = json.loads(Path(sys.argv[1]).read_text())
root = Path(cfg["data_root"]) / "asset_class=crypto/exchange=BINANCE/venue_type=futures_um"
for symbol in cfg["symbols"]:
    for kind in ("data_type=bar/freq=1m", "data_type=funding_rate"):
        folders = sorted((root / f"symbol={symbol}" / kind).glob("date=*" if "freq=" in kind else "freq=*/date=*"))
        files = sorted(folders[0].glob("*.parquet")) if folders else []
        print(symbol, kind, len(folders), [p.name for p in folders[:1] + folders[-1:]], flush=True)
        if files:
            table = pq.ParquetFile(files[0])
            first = next(table.iter_batches(batch_size=1)).to_pylist()
            print(first, flush=True)
