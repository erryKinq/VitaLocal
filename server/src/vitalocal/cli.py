"""Command line: `python -m vitalocal.cli import <export>` / `reprocess <batch_id>` / `drain` /
`reparse --gb-full` / `prune [--dry-run]`."""

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from vitalocal.config import get_settings
from vitalocal.db import get_engine
from vitalocal.deps import get_store
from vitalocal.ingest import retention, service, worker


def main() -> None:
    p = argparse.ArgumentParser(prog="vitalocal")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import a Gadgetbridge export ZIP")
    imp.add_argument("path", type=Path)
    rep = sub.add_parser("reprocess", help="re-run parsers for a batch (every file anew)")
    rep.add_argument("batch_id", type=int)
    sub.add_parser("drain", help="process every queued import now")
    full = sub.add_parser("reparse", help="parse the applied Gadgetbridge database snapshot again")
    full.add_argument("--gb-full", action="store_true", required=True, help="in full (as the nightly run)")
    pr = sub.add_parser("prune", help="drop raw bytes of superseded exports (retention rule)")
    pr.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    engine, store = get_engine(), get_store()
    if args.cmd == "prune":
        result = retention.prune(engine, store, get_settings().raw_retention_days, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return
    if args.cmd == "drain":
        print(json.dumps({"processed": worker.drain(engine, store)}))
        return
    if args.cmd == "reparse":
        print(json.dumps(service.reparse_latest_snapshot(engine, store), indent=2, default=str))
        return
    if args.cmd == "import":
        batch_id = service.import_file(engine, store, args.path)
    else:
        batch_id = args.batch_id
        service.process_batch(engine, store, batch_id, force=True)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, status, error, stats FROM ingest_batch WHERE id = :id"), {"id": batch_id}
        ).one()
    print(json.dumps(dict(row._mapping), indent=2, default=str))


if __name__ == "__main__":
    main()
