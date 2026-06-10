"""Top-level user strategy packages.

Each strategy is a subpackage (e.g. ``strategies/ma_crossover/``) holding its
``strategy.py`` (config + ``build_specs`` + signal logic + ``PLUGIN``), its
``config.yaml``, and a short ``README.md``. Strategies are registered in
``strategy_framework/registry.py`` and run via the top-level ``run_strategy.py``.
"""
