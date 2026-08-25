import os
from typing import List, Dict, Any
from pydub import AudioSegment


def convert_to_standard_format(input_path: str, output_path: str, sample_rate: int = 16000) -> str:
    """
    Standardizes input audio into 16kHz, 16-bit PCM, Mono WAV format.

    Args:
        input_path (str): Path to input audio file.
        output_path (str): Path to save standard WAV file.
        sample_rate (int): Target sample rate (default 16000 Hz).

    Returns:
        str: Output WAV path.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_frame_rate(sample_rate)
    audio = audio.set_channels(1)
    audio = audio.set_sample_width(2)  # 16-bit PCM = 2 bytes

    audio.export(output_path, format="wav")
    return output_path


def slice_audio(audio_path: str, start_time: float, end_time: float, output_path: str) -> str:
    """
    Slices a portion of an audio file between start_time and end_time (in seconds).

    Args:
        audio_path (str): Path to the source audio.
        start_time (float): Start time in seconds.
        end_time (float): End time in seconds.
        output_path (str): Path to save the extracted segment.

    Returns:
        str: Output sliced WAV path.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    audio = AudioSegment.from_file(audio_path)
    start_ms = int(start_time * 1000)
    end_ms = int(end_time * 1000)

    segment = audio[start_ms:end_ms]
    segment.export(output_path, format="wav")
    return output_path


def merge_and_save_chunks(
    segment_paths: List[str],
    max_duration: float = 30.0,
    output_prefix: str = "chunk",
    output_dir: str = "cs_dataset/train"
) -> List[Dict[str, Any]]:
    """
    Sequentially concatenates short VAD segments into longer chunks approaching max_duration (<= 30s)
    to optimize for Whisper training.

    Args:
        segment_paths (List[str]): List of paths to temporary audio segment files.
        max_duration (float): Maximum duration in seconds per merged chunk.
        output_prefix (str): Prefix used for output chunk file names.
        output_dir (str): Directory where merged chunk WAVs will be stored.

    Returns:
        List[Dict[str, Any]]: List of metadata dicts containing 'path' and 'duration'.
    """
    if not segment_paths:
        return []

    os.makedirs(output_dir, exist_ok=True)

    chunks: List[Dict[str, Any]] = []
    current_chunk = AudioSegment.empty()
    current_duration = 0.0
    chunk_index = 1

    def _save_chunk(audio_segment: AudioSegment, duration: float, idx: int):
        file_name = f"{output_prefix}_{idx:04d}.wav"
        chunk_path = os.path.join(output_dir, file_name)
        audio_segment.export(chunk_path, format="wav")
        chunks.append({
            "path": chunk_path,
            "duration": duration,
            "filename": file_name
        })

    for seg_path in segment_paths:
        if not os.path.exists(seg_path):
            continue

        seg_audio = AudioSegment.from_file(seg_path)
        seg_duration = len(seg_audio) / 1000.0

        # If a single segment is longer than max_duration on its own:
        if seg_duration >= max_duration:
            # Flush existing accumulated chunk first
            if current_duration > 0:
                _save_chunk(current_chunk, current_duration, chunk_index)
                chunk_index += 1
                current_chunk = AudioSegment.empty()
                current_duration = 0.0

            # Save the long segment as its own chunk
            _save_chunk(seg_audio, seg_duration, chunk_index)
            chunk_index += 1
            continue

        # If appending would exceed max_duration, save current chunk and start a new one
        if current_duration + seg_duration > max_duration and current_duration > 0:
            _save_chunk(current_chunk, current_duration, chunk_index)
            chunk_index += 1
            current_chunk = seg_audio
            current_duration = seg_duration
        else:
            current_chunk += seg_audio
            current_duration += seg_duration

    # Flush any remaining audio in the buffer
    if current_duration > 0:
        _save_chunk(current_chunk, current_duration, chunk_index)

    return chunks
