"""NVIDIA Spanish QuartzNet 15x5 character-CTC ONNX adapter."""

from .base_adapter import OnnxCTCAdapter


class QuartzNetAdapter(OnnxCTCAdapter):
    model_key = "quartznet15x5_es"
    features_size = 64
    subsampling_factor = 2
    blank_id = 35

    def encode_target(self, view):
        tokens = []
        for pos, char in enumerate(view.normalized):
            surface = "▁" if char == " " else char
            if surface not in self.vocab or surface == "<blk>":
                raise ValueError(f"QuartzNet target character {char!r} is out of vocabulary at normalized offset {pos}")
            original = view.char_to_original[pos]
            tokens.append(
                {
                    "id": self.vocab[surface],
                    "piece": surface,
                    "surface": char,
                    "norm_start": pos,
                    "norm_end": pos + 1,
                    "source_start": original,
                    "source_end": original + 1,
                    "word_index": view.char_to_word[pos],
                    "supported": True,
                }
            )
        return tokens
