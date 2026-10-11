"""Create temporary stage inputs from the bundled synthetic demo fixture."""
from __future__ import annotations

from pathlib import Path

from crr_compare import cli


def seed_internal_policy_stages(root: Path, end: int, fixture: str = "internal_policy_report") -> None:
    """Seed stages 02 through ``end`` in a fresh temporary root."""
    root.mkdir(parents=True, exist_ok=True)
    cli._seed_demo_fixture(fixture, root)
    cli.run_stages([f"{number:02d}" for number in range(2, end + 1)], root)
