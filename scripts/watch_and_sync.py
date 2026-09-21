"""Watch a SmartStation-exports folder and auto-sync any new CSVs to Katana.

The "always-on" side of the bridge. Runs indefinitely (or under a Windows
service / Task Scheduler task), polls the exports folder every N seconds,
and invokes sync_sales_to_katana.py the moment a new CSV lands.

Stdlib only. Restartable — state file remembers which files were already
processed, so a crash + restart doesn't double-post.

Usage:
    python scripts/watch_and_sync.py --folder "C:/exports" --interval 60
    python scripts/watch_and_sync.py --folder "C:/exports" --once --dry-run

To run as a Windows service:
    1. Install nssm (https://nssm.cc)  or use built-in Task Scheduler
    2. schtasks /Create /TN "KatanaSync" /TR "python watch_and_sync.py --folder C:\\exports" \
                /SC ONSTART /RU SYSTEM
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
STATE_DIR = SCRIPT_DIR / "state"
LOG_DIR = PROJECT_ROOT / "logs"
STATE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "watcher_state.json"
SYNC_SCRIPT = SCRIPT_DIR / "sync_sales_to_katana.py"


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed_files": {}}  # {path: {size, mtime, processed_at, status}}


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def file_signature(path):
    st = path.stat()
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def should_process(path, state):
    """A file is new if we haven't seen it OR its size/mtime changed since we did."""
    key = str(path.resolve())
    if key not in state["processed_files"]:
        return True
    prior = state["processed_files"][key]
    now = file_signature(path)
    if prior["size"] != now["size"] or prior["mtime"] != now["mtime"]:
        # File was modified after we processed — re-run (sync is idempotent)
        return True
    return False


def is_file_stable(path, min_age_seconds=5):
    """Don't process a CSV that's still being written to."""
    age = time.time() - path.stat().st_mtime
    return age >= min_age_seconds


def run_sync(csv_path, extra_args):
    cmd = [sys.executable, "-u", str(SYNC_SCRIPT), "--csv", str(csv_path)]
    cmd.extend(extra_args)
    log(f"Running: {' '.join(cmd[2:])}")  # skip python -u
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    # Tag last 5 lines of stdout for visibility
    tail = "\n    ".join(proc.stdout.strip().splitlines()[-6:])
    log(f"Exit={proc.returncode}\n    {tail}")
    if proc.stderr.strip():
        log(f"  stderr: {proc.stderr.strip()[:500]}")
    return proc.returncode == 0


def scan_once(folder, state, extra_args):
    """Look for new CSVs, sync them, update state."""
    csvs = sorted(folder.glob("*.csv"))
    log(f"Scanning {folder} — {len(csvs)} CSV files")
    new_count = 0
    for csv_path in csvs:
        if not should_process(csv_path, state):
            continue
        if not is_file_stable(csv_path):
            log(f"  {csv_path.name} — modified <5s ago, skipping until stable")
            continue
        new_count += 1
        log(f"  {csv_path.name} — new/changed, syncing")
        ok = run_sync(csv_path, extra_args)
        state["processed_files"][str(csv_path.resolve())] = {
            **file_signature(csv_path),
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "success": ok,
        }
        save_state(state)
    if new_count == 0:
        log("  no new/changed CSVs")
    return new_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True,
                    help="Folder to watch for SmartStation CSV exports")
    ap.add_argument("--interval", type=int, default=60,
                    help="Poll interval in seconds (default 60)")
    ap.add_argument("--once", action="store_true",
                    help="Do one scan and exit — useful for cron/Task Scheduler")
    ap.add_argument("--dry-run", action="store_true",
                    help="Pass --dry-run through to sync_sales_to_katana.py")
    ap.add_argument("--no-order", action="store_true",
                    help="Pass --no-order through (skip creating sales_orders)")
    ap.add_argument("--customer-id", type=int, default=None,
                    help="Pass --customer-id through")
    ap.add_argument("--location-id", type=int, default=None,
                    help="Pass --location-id through")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.exists() or not folder.is_dir():
        sys.exit(f"Folder not found: {folder}")

    if not SYNC_SCRIPT.exists():
        sys.exit(f"Missing sync script: {SYNC_SCRIPT}")

    # Build passthrough args
    extra = []
    if args.dry_run:
        extra.append("--dry-run")
    if args.no_order:
        extra.append("--no-order")
    if args.customer_id:
        extra.extend(["--customer-id", str(args.customer_id)])
    if args.location_id:
        extra.extend(["--location-id", str(args.location_id)])

    log(f"Watcher started. Folder={folder}  interval={args.interval}s  "
        f"once={args.once}  passthrough={extra}")

    state = load_state()
    try:
        if args.once:
            scan_once(folder, state, extra)
        else:
            while True:
                scan_once(folder, state, extra)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        log("Interrupted — exiting cleanly")


if __name__ == "__main__":
    main()
