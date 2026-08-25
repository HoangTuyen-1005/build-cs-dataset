import os
import glob
import uuid
import csv
import argparse
from typing import List, Dict, Any
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

    def __init__(self, data_type: str, speaker_id: str):
        self.data_type = data_type
        self.speaker_id = speaker_id

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
                writer.writerow(["id", "audio_path", "text", "duration", "type", "speaker_id"])

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
            ])

    def process_single_file(self, file_path: str):
        """
        Processes a single input audio file through the full pipeline.
        """
        file_basename = os.path.splitext(os.path.basename(file_path))[0]
        print(f"\n[Pipeline] Processing: {os.path.basename(file_path)}")

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

        # Step 4: Merge raw segments into chunks approaching ~30s duration
        merged_chunks = merge_and_save_chunks(
            segment_paths=raw_seg_paths,
            max_duration=self.max_chunk_duration,
            output_prefix=file_basename,
            output_dir=self.train_dir
        )
        print(f" -> Created {len(merged_chunks)} merged chunks (<= {self.max_chunk_duration}s) in '{self.train_dir}'.")

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
                "speaker_id": self.speaker_id,
            }
            self._append_metadata_record(record)
            print(f"   [+] {rel_audio_path} ({duration:.2f}s): \"{transcribed_text}\"")

    def run(self):
        """Scans the input directory and runs the pipeline across all audio files."""
        supported_exts = ("*.wav", "*.mp3", "*.m4a", "*.flac", "*.ogg", "*.aac")
        audio_files: List[str] = []
        for ext in supported_exts:
            audio_files.extend(glob.glob(os.path.join(self.input_dir, ext)))

        if not audio_files:
            print(f"[Info] No audio files found in input directory: '{self.input_dir}'")
            print(f"Please place audio files (.wav, .mp3, .m4a, .flac) into '{self.input_dir}' and rerun.")
            return

        print(f"Found {len(audio_files)} audio file(s) in '{self.input_dir}'. Starting pipeline...")
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
        required=True,
        help="Unique identifier for the speaker (e.g., 'SPK01', 'Speaker_A')",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    pipeline = DatasetBuilderPipeline(
        data_type=args.type,
        speaker_id=args.speaker_id
    )
    pipeline.run()


if __name__ == "__main__":
    main()
