import os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
HRIEGO = HERE.parents[1]
sys.path.insert(0, str(HRIEGO))
import paths as P
ROOT = P.streamtalk_root()
CKPT = ROOT / "checkpoints/pretrained/streamtalk_speaker_all_e0940_cfg3.pt"
FACE_CKPT = ROOT / "Scripts/FM/ckpt/split/SimpleSpeechModel/best.pt"
WAVLM = ROOT / "assets/wavlm-libri-clean-100h-large"
MODELS = HERE / "models"
M1 = HRIEGO / "opt/m1_memory"
KD_CACHE = M1 / "out/kd_cache"
RNG_PT = M1 / "logs/rng_prod.pt"
CLIPS_JSON = HRIEGO / "opt/t2_streamtalk_precision/clips.json"
def add_st_paths():
    sys.path.insert(0, str(ROOT / "vendor")); sys.path.insert(0, str(ROOT / "Scripts/FM")); sys.path.insert(0, str(ROOT / "Scripts"))
