import os
import re
from typing import Optional
import torch
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()


class FasterWhisperASR:
    """
    ASR transcriber utilizing faster-whisper.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        language: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ):
        self.model_name = model_name or os.getenv("WHISPER_MODEL", "large-v3-turbo")
        self.language = language or os.getenv("WHISPER_LANG", "vi")

        # Auto-detect CUDA availability or use configured device
        has_cuda = torch.cuda.is_available()
        user_device = device or os.getenv("DEVICE", "cuda").lower()

        if user_device == "cuda" and has_cuda:
            self.device = "cuda"
            self.compute_type = compute_type or os.getenv("COMPUTE_TYPE", "float16")
        else:
            self.device = "cpu"
            self.compute_type = "int8"

        print(f"[ASR] Loading Whisper model '{self.model_name}' on {self.device} ({self.compute_type})...")
        self.model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type
        )

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> str:
        """
        Transcribes the given audio file into text.

        Args:
            audio_path (str): Path to audio file.
            language (str, optional): Target language code (e.g. 'vi', 'en').

        Returns:
            str: Transcribed text string.
        """
        target_lang = language or self.language
        segments, _ = self.model.transcribe(
            audio_path,
            language=target_lang,
            condition_on_previous_text=False,
            beam_size=5,
            vad_filter=False,
        )

        texts = [segment.text.strip() for segment in segments if segment.text.strip()]
        full_text = " ".join(texts)
        return re.sub(r"\s+", " ", full_text).strip()
