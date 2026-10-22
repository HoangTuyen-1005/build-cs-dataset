"""
Script to download all audio tracks from MinIO links specified in the link metadata (.csv or .jsonl)
and save them into the `input/` folder with `room_id` prefixed to each filename.

Usage:
    python download_tracks.py
    python download_tracks.py --workers 8
    python download_tracks.py --limit 5  # Test first 5 files
"""

import os
import csv
import json
import time
import argparse
import urllib.request
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def extract_filename_from_url(url: str) -> str:
    """Extracts raw filename from URL (stripping query parameters)."""
    clean_path = url.split("?")[0]
    return clean_path.split("/")[-1]


def load_records(meta_path: str) -> List[Dict[str, Any]]:
    """Loads records from either a CSV or JSONL file."""
    meta_file = Path(meta_path)
    if not meta_file.exists():
        raise FileNotFoundError(f"Metadata file not found: {meta_path}")

    records = []
    if meta_file.suffix.lower() == ".csv":
        with meta_file.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
    elif meta_file.suffix.lower() == ".jsonl":
        with meta_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    else:
        raise ValueError(f"Unsupported file format: {meta_file.suffix}. Expected .csv or .jsonl")

    return records


def download_single_file(
    record: Dict[str, Any],
    output_dir: Path,
    overwrite: bool = False,
    max_retries: int = 3,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Downloads a single audio track with retry logic and room_id prefix in filename,
    organized into input/{room_id}/{user_id}/ subfolders.
    """
    url = record.get("url", "").strip()
    room_id = record.get("room_id", "").strip()
    user_id = record.get("user_id", "").strip() or record.get("participant_id", "").strip()

    if not url:
        return {"status": "error", "error": "Missing URL", "record": record}

    raw_filename = extract_filename_from_url(url)
    if not raw_filename:
        raw_filename = f"{user_id or 'unknown'}.ogg"

    # Extract user_id from filename if still missing
    if not user_id:
        m = re.search(r"(\d{15,22})", raw_filename)
        if m:
            user_id = m.group(1)
        else:
            user_id = "unknown_user"

    # Prefix with room_id
    if room_id and not raw_filename.startswith(f"{room_id}_"):
        target_filename = f"{room_id}_{raw_filename}"
    else:
        target_filename = raw_filename

    # Organize directly into user_id subfolder: input/{user_id}/
    target_folder = output_dir / user_id if user_id else output_dir / "unknown_user"
    target_folder.mkdir(parents=True, exist_ok=True)

    target_path = target_folder / target_filename
    temp_path = target_folder / f"{target_filename}.part"

    # Migration from older 2-level (room/user) or 1-level (room) or flat (output_dir) locations
    if room_id and user_id:
        old_2level_path = output_dir / room_id / user_id / target_filename
        if old_2level_path.exists() and not target_path.exists():
            old_2level_path.replace(target_path)
    if room_id:
        old_room_path = output_dir / room_id / target_filename
        if old_room_path.exists() and not target_path.exists():
            old_room_path.replace(target_path)

    flat_path = output_dir / target_filename
    if flat_path.exists() and not target_path.exists():
        flat_path.replace(target_path)

    # Skip if file already exists and overwrite is False
    if not overwrite and target_path.exists() and target_path.stat().st_size > 0:
        return {
            "status": "skipped",
            "filename": target_filename,
            "path": str(target_path),
            "size": target_path.stat().st_size,
        }

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "build-cs-dataset-crawler/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response, temp_path.open("wb") as out_file:
                chunk_size = 64 * 1024  # 64KB chunks
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)

            # Atomic rename from .part to target path
            if temp_path.exists():
                temp_path.replace(target_path)

            return {
                "status": "success",
                "filename": target_filename,
                "path": str(target_path),
                "size": target_path.stat().st_size,
            }

        except Exception as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

            if attempt == max_retries:
                return {
                    "status": "failed",
                    "filename": target_filename,
                    "error": str(e),
                    "url": url,
                    "room_id": room_id,
                }
            time.sleep(1.0 * attempt)

    return {"status": "failed", "filename": target_filename, "error": "Exceeded max retries"}


def reorganize_existing_files(output_dir: Path) -> int:
    """
    Reorganizes existing files in output_dir:
    From: output_dir/{room_id}/{user_id}/{filename}.ogg or output_dir/{room_id}/{filename}.ogg
    To:   output_dir/{user_id}/{filename}.ogg
    """
    supported_exts = {".ogg", ".wav", ".mp3", ".m4a", ".flac", ".aac"}
    moved_count = 0

    if not output_dir.exists():
        return 0

    # Collect all audio files in output_dir
    all_files = []
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in supported_exts:
                all_files.append(p)

    for item in all_files:
        parent_name = item.parent.name
        if re.match(r"^\d{15,22}$", parent_name) and item.parent.parent == output_dir:
            # Already in output_dir/{user_id}/
            continue

        # Extract user_id from filename or parent
        m = re.search(r"_(\d{15,22})-mic-audio", item.name)
        if not m:
            m = re.search(r"(\d{15,22})", item.name)
        user_id = m.group(1) if m else (parent_name if re.match(r"^\d{15,22}$", parent_name) else "unknown_user")

        user_folder = output_dir / user_id
        user_folder.mkdir(parents=True, exist_ok=True)

        dest_file = user_folder / item.name
        if item != dest_file:
            if not dest_file.exists():
                item.replace(dest_file)
                moved_count += 1
            else:
                item.unlink()

    # Clean up empty subdirectories
    for root, dirs, _ in os.walk(output_dir, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
            except OSError:
                pass

    return moved_count


def main():
    parser = argparse.ArgumentParser(
        description="Download audio files from MinIO links with room_id prefix."
    )
    parser.add_argument(
        "--meta",
        default="link/tracks_2026-10-12_2026-10-16.csv",
        help="Path to link metadata (.csv or .jsonl)",
    )
    parser.add_argument(
        "--output-dir",
        default="input",
        help="Target folder to save downloaded audio (default: 'input')",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of concurrent download threads (default: 8)",
    )
    parser.add_argument(
        "--num-rooms",
        "--num_rooms",
        type=int,
        default=None,
        help="Number of rooms to download from CSV in order of appearance (e.g. 5, 10)",
    )
    parser.add_argument(
        "--rooms",
        type=str,
        default=None,
        help="Comma-separated list of specific room_ids to download",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of tracks to download (useful for testing)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files instead of skipping",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds per download connection (default: 30)",
    )
    parser.add_argument(
        "--reorganize",
        action="store_true",
        help="Reorganize existing downloaded files into {room_id}/{user_id}/ folders and exit",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Reorganize any existing 1-level room files into {room_id}/{user_id}/
    moved_count = reorganize_existing_files(output_dir)
    if moved_count > 0:
        print(f"[Crawler] Automatically reorganized {moved_count} existing file(s) into {{room_id}}/{{user_id}}/ folders.")

    if args.reorganize:
        print(f"[Crawler] Reorganization complete in: {output_dir}")
        return

    print(f"[Crawler] Reading metadata from: {args.meta}")
    records = load_records(args.meta)
    print(f"[Crawler] Found {len(records)} total records in metadata.")

    # Filter by specific rooms or number of rooms from CSV
    if args.rooms:
        target_rooms = set(r.strip() for r in args.rooms.split(",") if r.strip())
        records = [r for r in records if r.get("room_id") in target_rooms]
        print(f"[Crawler] Filtered to {len(records)} tracks across {len(target_rooms)} specified room(s).")
    elif args.num_rooms and args.num_rooms > 0:
        unique_rooms = list(dict.fromkeys(r.get("room_id") for r in records if r.get("room_id")))
        selected_rooms = set(unique_rooms[: args.num_rooms])
        records = [r for r in records if r.get("room_id") in selected_rooms]
        print(f"[Crawler] Selected first {len(selected_rooms)} room(s) from CSV: {len(records)} tracks to download.")

    if args.limit:
        records = records[: args.limit]
        print(f"[Crawler] Limited to first {args.limit} records.")

    print(f"[Crawler] Output directory: {output_dir}")
    print(f"[Crawler] Concurrent threads: {args.workers}")
    print(f"[Crawler] Starting download...\n")

    start_time = time.time()
    results = {"success": 0, "skipped": 0, "failed": 0}
    failed_items = []

    def handle_result(res):
        status = res.get("status")
        if status == "success":
            results["success"] += 1
        elif status == "skipped":
            results["skipped"] += 1
        else:
            results["failed"] += 1
            failed_items.append(res)

    if tqdm:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(
                    download_single_file,
                    record=rec,
                    output_dir=output_dir,
                    overwrite=args.overwrite,
                    timeout=args.timeout,
                )
                for rec in records
            ]

            pbar = tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Downloading tracks",
                unit="file",
            )
            for future in pbar:
                res = future.result()
                handle_result(res)
                pbar.set_postfix(
                    ok=results["success"],
                    skip=results["skipped"],
                    fail=results["failed"],
                )
    else:
        # Fallback if tqdm is not installed
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(
                    download_single_file,
                    record=rec,
                    output_dir=output_dir,
                    overwrite=args.overwrite,
                    timeout=args.timeout,
                )
                for rec in records
            ]
            for idx, future in enumerate(as_completed(futures), 1):
                res = future.result()
                handle_result(res)
                if idx % 10 == 0 or idx == len(futures):
                    print(
                        f"[{idx}/{len(futures)}] ok={results['success']} "
                        f"skip={results['skipped']} fail={results['failed']}"
                    )

    elapsed = time.time() - start_time
    print(f"\n==================================================")
    print(f" Download Summary:")
    print(f"  • Total processed: {len(records)}")
    print(f"  • Succeeded:       {results['success']}")
    print(f"  • Skipped:         {results['skipped']} (already downloaded)")
    print(f"  • Failed:          {results['failed']}")
    print(f"  • Time elapsed:    {elapsed:.1f}s")
    print(f"  • Target folder:   {output_dir}")
    print(f"==================================================")

    if failed_items:
        fail_log = output_dir.parent / "download_failures.log"
        print(f"\n[Warning] {len(failed_items)} file(s) failed to download. Logging to {fail_log}")
        with fail_log.open("w", encoding="utf-8") as f:
            for item in failed_items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
