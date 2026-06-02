from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.ccxt_live.paper_live_runner import CcxtPaperLiveRunner
from nautilus_ext.ccxt_live.polling_config import CcxtPollingLiveConfig
from nautilus_ext.strategies.registry import build_signal_engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ccxt paper live from a strategy spec JSON.")
    parser.add_argument(
        "--spec",
        default=str(PROJECT_ROOT / "examples" / "strategy_specs" / "vwm_short.json"),
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    live_config = CcxtPollingLiveConfig(**payload["live"])
    strategy_spec = payload["strategy"]
    signal_engine = build_signal_engine(strategy_spec)
    runner = CcxtPaperLiveRunner(live_config, signal_engine)
    summary = runner.run()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
