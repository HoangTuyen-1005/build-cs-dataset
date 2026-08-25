import os
from typing import List, Tuple, Optional
from dotenv import load_dotenv

load_dotenv()

try:
    from fireredvad import FireRedVad, FireRedVadConfig
except ImportError:
    FireRedVad = None
    FireRedVadConfig = None


class FireRedVADDetector:
    """
    Wrapper for FireRedVAD Voice Activity Detection model.
    """

    _instance: Optional["FireRedVADDetector"] = None

    def __init__(
        self,
        model_dir: Optional[str] = None,
        use_gpu: Optional[bool] = None,
        speech_threshold: Optional[float] = None,
        min_speech_frame: Optional[int] = None,
        max_speech_frame: Optional[int] = None,
    ):
        if FireRedVad is None:
            raise ImportError(
                "FireRedVAD is not installed. Please run `pip install fireredvad` or `.\\setup_env.ps1`."
            )

        # Resolve model directory path from argument, .env, or fallback locations
        configured_dir = model_dir or os.getenv("VAD_MODEL_DIR", "pretrained_models/FireRedVAD/VAD")
        
        # Check if model exists in configured path or legacy path
        if not os.path.exists(configured_dir) and os.path.exists(os.path.join("FireRedVAD", configured_dir)):
            configured_dir = os.path.join("FireRedVAD", configured_dir)
        elif not os.path.exists(configured_dir) and os.path.exists("FireRedVAD/pretrained_models/FireRedVAD/VAD"):
            configured_dir = "FireRedVAD/pretrained_models/FireRedVAD/VAD"

        self.model_dir = configured_dir

        if use_gpu is None:
            device_cfg = os.getenv("DEVICE", "cuda").lower()
            self.use_gpu = device_cfg == "cuda"
        else:
            self.use_gpu = use_gpu

        self.speech_threshold = speech_threshold or float(
            os.getenv("VAD_SPEECH_THRESHOLD", "0.4")
        )
        self.min_speech_frame = min_speech_frame or int(
            os.getenv("VAD_MIN_SPEECH_FRAME", "20")
        )
        self.max_speech_frame = max_speech_frame or int(
            os.getenv("VAD_MAX_SPEECH_FRAME", "2000")
        )

        self._ensure_model_downloaded()
        self._load_model()

    def _ensure_model_downloaded(self):
        """Downloads the pretrained model from Hugging Face if not found locally."""
        if not os.path.exists(self.model_dir):
            print(f"[VAD] Model directory not found at '{self.model_dir}'. Downloading from Hugging Face Hub...")
            from huggingface_hub import snapshot_download

            target_base = os.path.dirname(os.path.abspath(self.model_dir))
            snapshot_download(repo_id="FireRedTeam/FireRedVAD", local_dir=target_base)

    def _load_model(self):
        """Instantiates the FireRedVad model."""
        config = FireRedVadConfig(
            use_gpu=self.use_gpu,
            speech_threshold=self.speech_threshold,
            min_speech_frame=self.min_speech_frame,
            max_speech_frame=self.max_speech_frame,
        )
        print(f"[VAD] Loading FireRedVAD model from '{self.model_dir}' (GPU={self.use_gpu})...")
        self.vad = FireRedVad.from_pretrained(self.model_dir, config)

    def detect(self, audio_path: str) -> List[Tuple[float, float]]:
        """
        Runs VAD on a 16kHz mono audio file.

        Args:
            audio_path (str): Path to 16kHz mono PCM WAV file.

        Returns:
            List[Tuple[float, float]]: List of (start_time, end_time) speech segment timestamps in seconds.
        """
        result, _ = self.vad.detect(audio_path)
        if isinstance(result, dict) and "timestamps" in result:
            return [(float(s), float(e)) for s, e in result["timestamps"]]

        if isinstance(result, list):
            segments = []
            for item in result:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    segments.append((float(item[0]), float(item[1])))
                elif isinstance(item, dict) and "start" in item and "end" in item:
                    segments.append((float(item["start"]), float(item["end"])))
            return segments

        return []

    @classmethod
    def get_instance(cls) -> "FireRedVADDetector":
        """Singleton accessor for FireRedVADDetector."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def get_speech_segments(audio_path: str, use_gpu: Optional[bool] = None) -> List[Tuple[float, float]]:
    """
    Convenience function to detect speech timestamps in an audio file using FireRedVAD.

    Args:
        audio_path (str): Path to 16kHz mono PCM WAV file.
        use_gpu (bool, optional): Whether to use GPU.

    Returns:
        List[Tuple[float, float]]: List of (start_time, end_time) speech timestamps in seconds.
    """
    detector = FireRedVADDetector.get_instance()
    if use_gpu is not None and detector.use_gpu != use_gpu:
        detector = FireRedVADDetector(use_gpu=use_gpu)
    return detector.detect(audio_path)
