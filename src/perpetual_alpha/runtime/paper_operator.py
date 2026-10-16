"""Trusted local OS operator only; no Agent resume/cancel endpoint."""
import argparse
import sqlite3
from pathlib import Path

from ..journal import canonical
from .paper_core import load_paper
from .paper_engine import PaperEngine
from .store import RuntimeStore


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", required=True)
    p.add_argument("--paper-config", default="config/paper-execution.synthetic.json")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("status")
    resume = commands.add_parser("resume")
    resume.add_argument("--generation", required=True)
    resume.add_argument("--pause-epoch", required=True, type=int)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("--decision-id", required=True)
    commands.add_parser("reconcile")
    for cmd in (resume, cancel, commands.choices["reconcile"]):
        cmd.add_argument("--request-id", required=True)
        cmd.add_argument("--actor", required=True)
    args = p.parse_args()
    path = Path(args.database).resolve()
    c = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)
    try:
        if not c.execute("SELECT 1 FROM paper_configuration WHERE id=1").fetchone():
            raise ValueError("Core must initialize an isolated Paper runtime first")
    finally:
        c.close()
    engine = PaperEngine(RuntimeStore(path), load_paper(args.paper_config))
    if args.action == "resume":
        result = engine.resume(args.request_id, args.generation, args.pause_epoch, args.actor)
    elif args.action == "reconcile":
        result = engine.reconcile(args.request_id, args.actor)
    elif args.action == "cancel":
        result = engine.cancel(args.decision_id, args.request_id, args.actor)
    else:
        result = engine.status()
    print(canonical(result))


if __name__ == "__main__":
    main()
