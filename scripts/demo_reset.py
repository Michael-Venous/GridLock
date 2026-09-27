"""Reversible local reset for demonstrating a new Santee filing with a preset parser.

    python3 scripts/demo_reset.py reset
    python3 scripts/demo_reset.py reset-ai
    python3 scripts/demo_reset.py restore

Reset keeps the 2024–2028 and 2025–2029 filings and preset Santee parser,
removes newer Santee filings, then restores a verified baseline snapshot.
The first run or changed build inputs require an offline rebuild to prepare that snapshot.
Raw PDFs and generated parser source files remain available for restoration. Backups live in the ignored
data/build/demo-resets directory. Restore replays the latest backup.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
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


def baseline_key(reg):
    digest = hashlib.sha256(json.dumps(reg, sort_keys=True).encode())
    # Invalidate the snapshot when build logic, source PDFs, or curated inputs change.
    paths = list((ROOT / "pipeline").glob("*.py")) + list((ROOT / "data").glob("*.json"))
    paths = [p for p in paths if p.name not in {"filings.json", "projects.json", "changes.json"}]
    paths += [ROOT / "data" / "raw" / f["file"] for f in reg["filings"]]
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        if path.exists(): digest.update(path.read_bytes())
    return digest.hexdigest()


def demo_instructions(ai=False):
    print("\nAI demo ready: all Santee filings and parsers are unregistered; Opus 5 will generate a fresh parser." if ai else "\nDemo ready: Santee 2024–2028 and 2025–2029 are loaded with the preset parser; 2026–2030 is absent.")
    print("Choose Public PDF link, then enter:")
    print("  https://www.scrtp.com/assets/pdfs/meeting-archives/scrtp-meeting-2026-03-11-presentation.pdf")
    print("  Utility to import: Santee Cooper | Filing date: 2026-03-11 | Edition: 2026-2030")
    print("Preview downloads the PDF and generates a parser using Opus 5 (default). AWS credentials are required; clear any different GRIDLOCK_BEDROCK_MODEL override before starting the server." if ai else "Preview downloads the PDF and uses the preset parser; no AI generation is needed. Review, then Register and rebuild.")
    print("Wait for Local dataset rebuilt, then Reload Seamline. Under-three-minute completion is not guaranteed.")


def reset(ai=False):
    reg = registry.load_registry()
    original = json.dumps(reg, sort_keys=True)
    baseline = json.loads((ROOT / "scripts" / "demo_santee_baseline.json").read_text())
    if ai:
        baseline = {"filings": []}
    kept_ids = {f["id"] for f in baseline["filings"]}
    removed = [f for f in reg["filings"] if f["plan"] == DEMO_PLAN and f["id"] not in kept_ids]
    parser_ids = {pid for pid, p in reg["parsers"].items() if p["plan"] == DEMO_PLAN}
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
    if not ai:
        reg["parsers"]["santee"] = baseline["parser"]
    reg["filings"].extend(baseline["filings"])
    reg["filings"].sort(key=lambda f: (f["date"], f["id"]))
    missing = [f["file"] for f in baseline["filings"] if not (ROOT / "data" / "raw" / f["file"]).is_file()]
    if missing:
        raise SystemExit("Download the older Santee PDFs into data/raw before reset: " + ", ".join(missing))
    try:
        registry.save_registry(reg)
        clean = BACKUPS / "clean" / baseline_key(reg)
        manifest_path = clean / "manifest.json"
        cached = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
        valid = cached.get("hashes") and all((clean / rel).is_file() and
            hashlib.sha256((clean / rel).read_bytes()).hexdigest() == cached["hashes"].get(rel)
            for rel in OUTPUTS)
        if valid:
            print("Restoring verified clean dataset snapshot…", flush=True)
            restore_files(clean)
        else:
            print("Preparing clean snapshot (first run or changed build inputs)…", flush=True)
            subprocess.run([sys.executable, "pipeline/build.py", "--offline"], cwd=ROOT, check=True)
            clean.mkdir(parents=True, exist_ok=True)
            for relative in OUTPUTS:
                copy_to(clean, relative)
            (clean / "manifest.json").write_text(json.dumps({"files": list(OUTPUTS), "hashes": {
                rel: hashlib.sha256((clean / rel).read_bytes()).hexdigest() for rel in OUTPUTS}}, indent=2))
    except BaseException:
        restore_files(snapshot)
        print(f"Reset failed; original files restored from {snapshot}", file=sys.stderr)
        raise
    if original != json.dumps(reg, sort_keys=True) or not (BACKUPS / "latest").exists():
        (BACKUPS / "latest").write_text(stamp + "\n")
    print(f"Removed {len(removed)} Santee filing(s) and all Santee parser registrations for the AI demo." if ai else f"Removed {len(removed)} newer Santee filing(s); retained two older editions and the preset parser.")
    print(f"Backup: {snapshot}\nRestore with: python3 scripts/demo_reset.py restore")
    demo_instructions(ai)


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
    commands.add_parser("reset", help="Restore older Santee filings and preset parser; remove the newest demo filing")
    commands.add_parser("reset-ai", help="Remove all Santee filings and parsers for fresh Opus 5 generation")
    commands.add_parser("restore", help="Restore the files from the latest successful reset")
    args = parser.parse_args()
    registry.LOCK.parent.mkdir(parents=True, exist_ok=True)
    with registry.LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command in ("reset", "reset-ai"):
            reset(ai=args.command == "reset-ai")
        else:
            restore()


if __name__ == "__main__":
    main()
