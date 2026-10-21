"""Trusted worker for nine fixed preparation interruption cases.

The worker has no environment activation, dynamic callback loader or production
CLI switch. It emits one safe boundary acknowledgement and pauses with a fixed
90-second POSIX alarm until its exact parent-owned process is terminated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys


RULES_VERSION = "workspace-restore-prepare-1"
BOUNDARIES = (
    "plan_partial", "plan_durable", "intent_durable", "owner_partial", "owner_durable",
    "metadata_partial", "metadata_linked", "preparation_ready", "data_staging_preserved",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trusted fixed-boundary restore preparation worker.")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--boundary", required=True, choices=BOUNDARIES)
    arguments = parser.parse_args(argv)
    try:
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(90)
        from . import workspace_restore_job

        selected = arguments.boundary
        hook = "staging_partial" if selected == "data_staging_preserved" else selected
        ordinal = 3 if selected == "data_staging_preserved" else 1 if selected.startswith("metadata_") else None
        reached = False

        def checkpoint(boundary: str, *, item_index: int | None = None) -> None:
            nonlocal reached
            if boundary != hook or item_index != ordinal:
                return
            if reached:
                raise RuntimeError("The selected fixed boundary was reached more than once")
            reached = True
            message = {"rules_version": RULES_VERSION, "boundary": selected, "checkpoint_reached": True}
            sys.stdout.write(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            while True:
                signal.pause()

        workspace_restore_job._checkpoint = checkpoint
        workspace_restore_job.prepare_restore(
            arguments.bundle, arguments.destination, arguments.job, operation_id=arguments.operation_id,
        )
        if selected == "data_staging_preserved":
            workspace_restore_job.step_restore(arguments.job, limit=16)
        raise RuntimeError("The selected fixed boundary was not observed")
    except (Exception, KeyboardInterrupt):
        sys.stderr.write("[restore_prepare_worker_failed] Fixed-boundary worker could not pause.\n")
        return 2
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
