import os
import glob
import uuid
import csv
import argparse
import re
from typing import List, Dict, Any, Optional, Set
from dotenv import load_dotenv
from tqdm import tqdm

from converter import convert_to_standard_format, slice_audio, merge_and_save_chunks
from vad import get_speech_segments
from asr import FasterWhisperASR

load_dotenv()


class DatasetBuilderPipeline:
    """
    Main orchestrator pipeline to process raw audio inputs, perform VAD segmentation,
    merge segments into ~30s chunks, transcribe using Whisper, and generate metadata.csv.
    """

    def __init__(
        self,
        data_type: str,
        speaker_id: str = "auto",
        num_rooms: Optional[int] = None,
        rooms: Optional[str] = None,
        meta_file_for_rooms: Optional[str] = None,
        num_users: Optional[int] = None,
        users: Optional[str] = None,
    ):
        self.data_type = data_type
        self.speaker_id = speaker_id
        self.num_rooms = num_rooms
        self.rooms = rooms
        self.meta_file_for_rooms = meta_file_for_rooms or os.getenv(
            "ROOM_META_FILE", "link/tracks_2026-10-12_2026-10-16.csv"
        )
        self.num_users = num_users
        self.users = users

        # Load directory paths and configurations from .env
        self.input_dir = os.getenv("INPUT_DIR", "input")
        self.temp_seg_dir = os.getenv("TEMP_SEG_DIR", "seg_temp")
        self.output_dir = os.getenv("OUTPUT_DIR", "cs_dataset")
        self.train_dir = os.getenv("TRAIN_DIR", os.path.join(self.output_dir, "train"))
        self.metadata_file = os.getenv("METADATA_FILE", os.path.join(self.output_dir, "metadata.csv"))
        self.max_chunk_duration = float(os.getenv("MAX_CHUNK_DURATION", "30.0"))

        # Ensure all required directories exist
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.temp_seg_dir, exist_ok=True)
        os.makedirs(self.train_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

        # Initialize metadata CSV file with headers if not present or empty
        self._init_metadata_file()

        # Initialize ASR model
        self.asr = FasterWhisperASR()

    def _init_metadata_file(self):
        """Creates the metadata.csv file with headers if it does not exist or is empty."""
        needs_header = not os.path.exists(self.metadata_file) or os.path.getsize(self.metadata_file) == 0
        if needs_header:
            with open(self.metadata_file, mode="w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                writer.writerow(["id", "audio_path", "text", "duration", "type", "speaker_id", "is_verified", "verified_by"])

    def _append_metadata_record(self, record: Dict[str, Any]):
        """Appends a new sample record to the metadata.csv file."""
        with open(self.metadata_file, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow([
                record["id"],
                record["audio_path"],
                record["text"],
                f"{record['duration']:.2f}",
                record["type"],
                record["speaker_id"],
                record.get("is_verified", False),
                record.get("verified_by", ""),
            ])

    def _resolve_speaker_id(self, file_path: str) -> str:
        """Resolves the speaker_id either from user CLI or auto-detected from folder or filename."""
        if self.speaker_id and self.speaker_id.lower() != "auto":
            return self.speaker_id
        # Check immediate parent folder (e.g. input/{room_id}/{user_id}/file.ogg)
        parent = os.path.basename(os.path.dirname(file_path))
        if re.match(r"^\d{15,22}$", parent):
            return parent
        filename = os.path.basename(file_path)
        m = re.search(r"_(\d{15,22})-mic-audio", filename)
        if m:
            return m.group(1)
        m2 = re.search(r"(\d{15,22})", filename)
        if m2:
            return m2.group(1)
        return "speaker_001"

    def _resolve_room_id(self, file_path: str) -> str:
        """Resolves the room_id from input filename or directory structure."""
        filename = os.path.basename(file_path)
        m = re.match(r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", filename)
        if m:
            return m.group(1)
        rel = os.path.relpath(file_path, self.input_dir).replace("\\", "/")
        parts = rel.split("/")
        if len(parts) >= 2 and re.match(r"^([0-9a-fA-F-]{36})$", parts[0]):
            return parts[0]
        return "default_room"

    def _get_target_rooms(self) -> Optional[Set[str]]:
        """
        Resolves the set of room_ids to filter by, based on --rooms or --num_rooms.
        """
        if self.rooms:
            rooms_list = [r.strip() for r in self.rooms.split(",") if r.strip()]
            return set(rooms_list)

        if self.num_rooms is not None and self.num_rooms > 0:
            if os.path.exists(self.meta_file_for_rooms):
                unique_rooms = []
                with open(self.meta_file_for_rooms, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rid = row.get("room_id", "").strip()
                        if rid and rid not in unique_rooms:
                            unique_rooms.append(rid)
                selected = unique_rooms[: self.num_rooms]
                print(f"[Pipeline] Selected first {len(selected)} room(s) from '{self.meta_file_for_rooms}'.")
                return set(selected)
            else:
                # Fallback: inspect subdirectories inside input_dir
                subdirs = [
                    d for d in os.listdir(self.input_dir)
                    if os.path.isdir(os.path.join(self.input_dir, d))
                ]
                selected = subdirs[: self.num_rooms]
                print(f"[Pipeline] Selected first {len(selected)} room directory(ies) from '{self.input_dir}'.")
                return set(selected)

        return None

    def _get_target_users(self) -> Optional[Set[str]]:
        """Resolves the set of user_ids to filter by, based on --users or --num_users."""
        if self.users:
            users_list = [u.strip() for u in self.users.split(",") if u.strip()]
            return set(users_list)

        if self.num_users is not None and self.num_users > 0:
            user_dirs = sorted([
                d for d in os.listdir(self.input_dir)
                if os.path.isdir(os.path.join(self.input_dir, d)) and not d.startswith(".")
            ])
            selected = user_dirs[: self.num_users]
            print(f"[Pipeline] Selected first {len(selected)} user(s): {selected}")
            return set(selected)

        return None

    def process_single_file(self, file_path: str):
        """
        Processes a single input audio file through the full pipeline.
        """
        file_basename = os.path.splitext(os.path.basename(file_path))[0]
        current_speaker = self._resolve_speaker_id(file_path)
        current_room = self._resolve_room_id(file_path)
        print(f"\n[Pipeline] Processing: {os.path.basename(file_path)} (Room: {current_room}, Speaker: {current_speaker})")

        # Step 1: Standardize Audio to 16kHz Mono 16-bit WAV
        std_wav_path = os.path.join(self.temp_seg_dir, f"{file_basename}_std.wav")
        convert_to_standard_format(file_path, std_wav_path)

        # Step 2: Voice Activity Detection (VAD)
        segments = get_speech_segments(std_wav_path)
        if not segments:
            print(f"[Warning] No speech detected by VAD in '{file_path}'. Skipping.")
            return

        print(f" -> VAD detected {len(segments)} speech segments.")

        # Step 3: Extract raw VAD segments into temporary directory
        raw_seg_paths: List[str] = []
        for idx, (start_sec, end_sec) in enumerate(segments):
            seg_filename = f"{file_basename}_rawseg_{idx:04d}.wav"
            seg_path = os.path.join(self.temp_seg_dir, seg_filename)
            slice_audio(std_wav_path, start_sec, end_sec, seg_path)
            raw_seg_paths.append(seg_path)

        # Step 4: Merge raw segments into chunks approaching ~30s duration in cs_dataset/train/{speaker_id}/
        target_chunk_dir = os.path.join(self.train_dir, current_speaker)
        os.makedirs(target_chunk_dir, exist_ok=True)

        merged_chunks = merge_and_save_chunks(
            segment_paths=raw_seg_paths,
            max_duration=self.max_chunk_duration,
            output_prefix=file_basename,
            output_dir=target_chunk_dir,
        )
        print(f" -> Created {len(merged_chunks)} merged chunks (<= {self.max_chunk_duration}s) in '{target_chunk_dir}'.")

        # Step 5: Transcribe merged chunks and record metadata
        for chunk in merged_chunks:
            chunk_path = chunk["path"]
            duration = chunk["duration"]

            # Format relative audio path (e.g. train/filename_0001.wav)
            rel_audio_path = os.path.relpath(chunk_path, self.output_dir).replace("\\", "/")

            # ASR Transcription
            transcribed_text = self.asr.transcribe(chunk_path)

            # Metadata Record
            record = {
                "id": str(uuid.uuid4()),
                "audio_path": rel_audio_path,
                "text": transcribed_text,
                "duration": duration,
                "type": self.data_type,
                "speaker_id": current_speaker,
                "is_verified": False,
            }
            self._append_metadata_record(record)
            print(f"   [+] {rel_audio_path} ({duration:.2f}s): \"{transcribed_text}\"")

    def run(self):
        """Scans the input directory and runs the pipeline across all audio files."""
        supported_exts = ("*.wav", "*.mp3", "*.m4a", "*.flac", "*.ogg", "*.aac")
        audio_files: List[str] = []
        for ext in supported_exts:
            # Recursively scan root and any room_id subdirectories
            audio_files.extend(glob.glob(os.path.join(self.input_dir, "**", ext), recursive=True))

        if not audio_files:
            print(f"[Info] No audio files found in input directory: '{self.input_dir}'")
            print(f"Please place audio files (.wav, .mp3, .m4a, .flac) into '{self.input_dir}' and rerun.")
            return

        # Filter audio files by target rooms if specified
        target_rooms = self._get_target_rooms()
        if target_rooms:
            filtered_files = []
            for f in audio_files:
                rid = self._resolve_room_id(f)
                if rid in target_rooms:
                    filtered_files.append(f)
            audio_files = filtered_files
            print(f"[Pipeline] Filtered to {len(audio_files)} audio file(s) across {len(target_rooms)} selected room(s).")

        # Filter audio files by target users if specified
        target_users = self._get_target_users()
        if target_users:
            filtered_files = []
            for f in audio_files:
                uid = self._resolve_speaker_id(f)
                if uid in target_users:
                    filtered_files.append(f)
            audio_files = filtered_files
            print(f"[Pipeline] Filtered to {len(audio_files)} audio file(s) across {len(target_users)} selected user(s).")

        if not audio_files:
            print(f"[Info] No audio files matching the selected criteria in '{self.input_dir}'.")
            return

        print(f"Found {len(audio_files)} audio file(s) to process. Starting pipeline...")
        for file_path in tqdm(audio_files, desc="Building Dataset"):
            try:
                self.process_single_file(file_path)
            except Exception as e:
                print(f"[Error] Failed to process '{file_path}': {e}")

        print(f"\n[Success] Dataset build complete! Results saved to '{self.metadata_file}'.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build standard ASR dataset from raw audio using FireRedVAD and Faster-Whisper."
    )
    parser.add_argument(
        "--type",
        type=str,
        required=True,
        help="Dataset category/type (e.g., 'cs', 'mono_vi', 'en_accent_vi')",
    )
    parser.add_argument(
        "--speaker_id",
        type=str,
        default="auto",
        help="Speaker identifier (e.g., 'SPK01', or 'auto' to extract participant_id from filename)",
    )
    parser.add_argument(
        "--num_rooms",
        "--num-rooms",
        type=int,
        default=None,
        help="Number of rooms to process from the CSV list (e.g. 5, 10)",
    )
    parser.add_argument(
        "--rooms",
        type=str,
        default=None,
        help="Comma-separated list of specific room_ids to process",
    )
    parser.add_argument(
        "--num_users",
        "--num-users",
        type=int,
        default=None,
        help="Number of user_id folders to process from input/ (e.g. 5, 10)",
    )
    parser.add_argument(
        "--users",
        type=str,
        default=None,
        help="Comma-separated list of specific user_ids to process",
    )
    parser.add_argument(
        "--meta",
        type=str,
        default="link/tracks_2026-10-12_2026-10-16.csv",
        help="Path to CSV file containing room_id list (default: 'link/tracks_2026-10-12_2026-10-16.csv')",
    )
    parser.add_argument(
        "--reorganize-dataset",
        action="store_true",
        help="Reorganize existing cs_dataset/train chunks into {room_id}/{user_id}/ folders and update metadata.csv",
    )
    return parser.parse_args()


def reorganize_existing_dataset(output_dir: str = "cs_dataset") -> int:
    """
    Reorganizes existing audio files in cs_dataset/train/:
    From: cs_dataset/train/{room_id}/{user_id}/{chunk_filename}.wav or cs_dataset/train/{chunk_filename}.wav
    To:   cs_dataset/train/{user_id}/{chunk_filename}.wav
    And updates metadata.csv accordingly.
    """
    train_dir = os.path.join(output_dir, "train")
    meta_path = os.path.join(output_dir, "metadata.csv")

    if not os.path.exists(train_dir) or not os.path.exists(meta_path):
        return 0

    with open(meta_path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    moved_count = 0
    updated_rows = []

    for row in rows:
        old_rel = row.get("audio_path", "").strip().replace("\\", "/")
        base_name = os.path.basename(old_rel)

        # Extract user_id from chunk filename or row
        m_user = re.search(r"(\d{15,22})", base_name)
        user_id = m_user.group(1) if m_user else (row.get("speaker_id") or "unknown_speaker")

        expected_rel = f"train/{user_id}/{base_name}"
        if old_rel == expected_rel and os.path.exists(os.path.join(output_dir, expected_rel)):
            updated_rows.append(row)
            continue

        # Destination folder: cs_dataset/train/{user_id}
        dest_folder = os.path.join(train_dir, user_id)
        os.makedirs(dest_folder, exist_ok=True)

        old_file = os.path.join(output_dir, old_rel)
        new_file = os.path.join(dest_folder, base_name)

        if os.path.exists(old_file) and not os.path.exists(new_file):
            os.replace(old_file, new_file)
            moved_count += 1
        elif os.path.exists(old_file) and os.path.exists(new_file) and old_file != new_file:
            os.remove(old_file)

        row["audio_path"] = expected_rel
        row["speaker_id"] = user_id
        updated_rows.append(row)

    if moved_count > 0 or any(r["audio_path"] != old_r["audio_path"] for r, old_r in zip(updated_rows, rows)):
        tmp_meta = meta_path + ".tmp"
        with open(tmp_meta, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(updated_rows)
        os.replace(tmp_meta, meta_path)

    # Clean up empty subdirectories in train
    for root, dirs, _ in os.walk(train_dir, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
            except OSError:
                pass

    return moved_count


def main():
    args = parse_args()

    # Reorganize existing dataset if requested or to ensure consistency
    if getattr(args, "reorganize_dataset", False):
        count = reorganize_existing_dataset(os.getenv("OUTPUT_DIR", "cs_dataset"))
        print(f"[Success] Reorganized {count} chunk file(s) into {{user_id}}/ and updated metadata.csv.")
        return

    # Automatically ensure existing files in cs_dataset/train are in {user_id}/ structure
    reorganize_existing_dataset(os.getenv("OUTPUT_DIR", "cs_dataset"))

    pipeline = DatasetBuilderPipeline(
        data_type=args.type,
        speaker_id=args.speaker_id,
        num_rooms=args.num_rooms,
        rooms=args.rooms,
        meta_file_for_rooms=args.meta,
        num_users=args.num_users,
        users=args.users,
    )
    pipeline.run()


if __name__ == "__main__":
    main()
