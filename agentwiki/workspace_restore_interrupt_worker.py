"""Trusted fixed-boundary worker used only by the interruption demonstration.

This module has no environment switch, public production-CLI option or dynamic
callback loader. A fixed private checkpoint is replaced inside this one child
process. The only stdout message acknowledges an allowlisted boundary; the
worker then remains paused until the driver terminates this exact process. A
fixed POSIX alarm bounds its lifetime if the driver disappears.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
from typing import Any


RULES_VERSION = "workspace-restore-interrupt-1"
BOUNDARIES = ("staging_partial", "staging_synced", "target_linked", "data_ready", "completed")
_DATA_BOUNDARIES = frozenset(BOUNDARIES[:3])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trusted fixed-boundary restore interruption worker.")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--boundary", choices=BOUNDARIES, required=True)
    arguments = parser.parse_args(argv)
    try:
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(90)
        from . import workspace_restore_job

        reached = False

        def checkpoint(boundary: str, *, item_index: int | None = None) -> None:
            nonlocal reached
            if boundary != arguments.boundary:
                return
            if boundary in _DATA_BOUNDARIES and item_index != 3:
                return
            if boundary not in _DATA_BOUNDARIES and item_index is not None:
                return
            if reached:
                raise RuntimeError("The fixed interruption boundary was reached more than once")
            reached = True
            message: dict[str, Any] = {
                "rules_version": RULES_VERSION,
                "boundary": boundary,
                "checkpoint_reached": True,
            }
            sys.stdout.write(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            while True:
                signal.pause()

        workspace_restore_job._checkpoint = checkpoint
        workspace_restore_job.step_restore(arguments.job, limit=16)
        # A selected boundary must pause before the step can return. A normal
        # result is deliberately never printed as a substitute for that event.
        raise RuntimeError("The selected interruption boundary was not observed")
    except (Exception, KeyboardInterrupt):
        sys.stderr.write("[restore_interrupt_worker_failed] Fixed-boundary worker could not pause.\n")
        return 2
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
