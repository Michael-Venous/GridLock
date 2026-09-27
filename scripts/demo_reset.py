"""Reversible local reset for demonstrating Santee parser generation and import.

    python3 scripts/demo_reset.py reset
    python3 scripts/demo_reset.py restore

Reset unregisters every Santee filing and parser, then rebuilds offline.
Raw PDFs and parser source files remain available for restoration, but the
importer cannot select an unregistered parser. Backups live in the ignored
data/build/demo-resets directory. Restore replays the latest backup.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import registry  # noqa: E402

BACKUPS = ROOT / "data" / "build" / "demo-resets"
OUTPUTS = ("data/filings.json", "data/projects.json", "data/changes.json", "data/env/evidence.geojson")


def copy_to(snapshot, relative):
    source = ROOT / relative
    if source.is_file():
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def restore_files(snapshot):
    manifest = json.loads((snapshot / "manifest.json").read_text())
    for relative in manifest["files"]:
        source = snapshot / relative
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        shutil.copy2(source, staged)
        os.replace(staged, target)
    return manifest


DEMO_PLAN = "santee"


def reset():
    reg = registry.load_registry()
    removed = [f for f in reg["filings"] if f["plan"] == DEMO_PLAN]
    parser_ids = {pid for pid, p in reg["parsers"].items() if p["plan"] == DEMO_PLAN}
    if not removed and not parser_ids:
        print("Santee filings and parsers are already unregistered; no files changed.")
        return
    if any(f["plan"] != DEMO_PLAN and f["parser"] in parser_ids for f in reg["filings"]):
        raise SystemExit("A Santee parser is shared with another plan; no files changed.")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot = BACKUPS / stamp
    snapshot.mkdir(parents=True)
    files = [p for p in OUTPUTS if (ROOT / p).is_file()]
    for relative in files:
        copy_to(snapshot, relative)
    (snapshot / "manifest.json").write_text(json.dumps({"plan": DEMO_PLAN, "files": files}, indent=2) + "\n")

    reg["filings"] = [f for f in reg["filings"] if f["plan"] != DEMO_PLAN]
    for pid in parser_ids:
        reg["parsers"].pop(pid)
    try:
        registry.save_registry(reg)
        subprocess.run([sys.executable, "pipeline/build.py", "--offline"], cwd=ROOT, check=True)
    except BaseException:
        restore_files(snapshot)
        print(f"Reset failed; original files restored from {snapshot}", file=sys.stderr)
        raise
    (BACKUPS / "latest").write_text(stamp + "\n")
    print(f"Unregistered {len(removed)} Santee filing(s) and {len(parser_ids)} parser(s).")
    print(f"Backup: {snapshot}\nRestore with: python3 scripts/demo_reset.py restore")


def restore():
    pointer = BACKUPS / "latest"
    if not pointer.is_file():
        raise SystemExit("No successful demo reset backup was found.")
    stamp = pointer.read_text().strip()
    if not stamp.isalnum() or not (BACKUPS / stamp / "manifest.json").is_file():
        raise SystemExit("The latest demo backup is missing or invalid.")
    snapshot = BACKUPS / stamp
    manifest = restore_files(snapshot)
    print(f"Restored {manifest.get('plan', manifest.get('filing'))} registration and generated data from {snapshot}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reset", help="Back up, unregister all Santee filings/parsers, and rebuild offline")
    commands.add_parser("restore", help="Restore the files from the latest successful reset")
    args = parser.parse_args()
    registry.LOCK.parent.mkdir(parents=True, exist_ok=True)
    with registry.LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "reset":
            reset()
        else:
            restore()


if __name__ == "__main__":
    main()
