"""qvalid: statistical validation of trading strategies from a trade log.

The public surface is documented in ``docs/01_escopo_e_arquitetura.md``. The
layering rule is enforced by convention and by import direction: ``core`` never
imports ``adapters`` or ``report``.
"""

from __future__ import annotations

__version__ = "1.9.0"

__all__ = ["__version__"]
