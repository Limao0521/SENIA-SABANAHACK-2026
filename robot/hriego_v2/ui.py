from __future__ import annotations

import textwrap
import time
from typing import Any


class HriegoDashboard:
    """Reuse Face's annotated 50/50 dashboard and replace its right pane with HRI state."""

    def __init__(self, settings) -> None:
        import cv2
        import numpy as np
        from live.institutional_renderer import render_institutional_dashboard

        self.cv2 = cv2
        self.np = np
        self.settings = settings
        self.render_face = render_institutional_dashboard
        self.width = int(settings.window_width)
        self.height = int(settings.window_height)
        self.window_name = "HRIEGO v2 | Recepción institucional autónoma"
        self.fps_times: list[float] = []

    def create(self) -> None:
        self.cv2.namedWindow(self.window_name, self.cv2.WINDOW_NORMAL)
        self.cv2.resizeWindow(self.window_name, self.width, self.height)

    def render(self, perception, status: dict[str, Any]) -> Any:
        profile_state = perception.profile_snapshot or {}
        frame = self.render_face(
            perception.result,
            profile_state.get("cards", []),
            window_width=self.width,
            window_height=self.height,
            stream_status=perception.camera_status,
            profile_status=profile_state.get("status", "CONNECTING"),
            profile_latency_ms=profile_state.get("query_latency_ms", 0.0),
            render_fps=self._fps(),
        )[0]
        left_w = self.width // 2
        self.cv2.rectangle(frame, (left_w + 1, 0), (self.width - 1, self.height - 1), (31, 35, 41), -1)
        self._text(frame, "HRIEGO · AGENTE DE RECEPCIÓN", (left_w + 18, 30), (238, 241, 245), 0.62, 2)
        self._text(frame, "Una identidad · FSM autónoma · motor no ejecutado", (left_w + 18, 53), (86, 205, 124), 0.43)

        p = status.get("profile") or {}
        run = status.get("run_id") or "—"
        session = status.get("session_id") or "—"
        self._section(frame, left_w, 88, "A · PERFIL INSTITUCIONAL")
        self._line(frame, left_w, 116, "Person ID", p.get("person_id", "—"))
        self._line(frame, left_w, 139, "Nombre", p.get("display_name", "—"))
        self._line(frame, left_w, 162, "Rango", p.get("rank_name") or p.get("rank_code") or "neutral")
        self._line(frame, left_w, 185, "Reconocimiento", p.get("identity_status", "—"))
        self._line(frame, left_w, 208, "Visit ID", (status.get("visit_id") or "—")[:28])
        self._line(frame, left_w, 231, "Run ID", run[:28])

        self._section(frame, left_w, 264, "B · CONTEXT MANAGER")
        self._line(frame, left_w, 292, "Estado FSM", f"{status.get('state', 'IDLE')} · {status.get('status', 'idle')}")
        self._line(frame, left_w, 315, "Política", status.get("policy") or "—")
        self._line(frame, left_w, 338, "Identidad agente", status.get("agent_version", "—"))
        self._line(frame, left_w, 361, "Política estado", status.get("state_policy_version", "—"))
        self._line(frame, left_w, 384, "Sesión", session[:28])
        transitions = " → ".join(item.get("state", "?") for item in status.get("transitions", [])) or "sin transiciones"
        self._line(frame, left_w, 407, "Transiciones", transitions[:72])
        self._line(frame, left_w, 430, "Recuperación RAG", status.get("retrieval_status", "—"))

        self._section(frame, left_w, 462, "C · QWEN / CONTRATO RCP-01C")
        self._line(frame, left_w, 490, "Estado validación", status.get("validation", "—"))
        self._line(frame, left_w, 513, "Intención", status.get("motor_intent", "NONE"))
        self._line(frame, left_w, 536, "Trigger", status.get("trigger_text") or "—")
        qm = status.get("qwen_metrics") or {}
        self._line(frame, left_w, 559, "Inferencia", f"{qm.get('wall_ms', qm.get('wall_s', '—'))} ms")
        self._line(frame, left_w, 582, "Tokens entrada/salida", f"{qm.get('prompt_tokens', '—')} / {qm.get('completion_tokens', '—')}")
        self._text(frame, "Discurso generado:", (left_w + 18, 612), (160, 170, 182), 0.43)
        self._wrapped(frame, status.get("speech", "—"), left_w + 18, 634, self.width - left_w - 34, 20, 0.43, 5)

        supertonic = self.settings.tts_backend == "supertonic"
        self._section(frame, left_w, 744, "D · TTS / REPRODUCCIÓN")
        self._line(frame, left_w, 772, "Síntesis", status.get("tts_status", "idle"))
        self._line(frame, left_w, 795, "Reproducción", status.get("playback_status", "idle"))
        tm = status.get("tts_metrics") or {}
        self._line(frame, left_w, 818, "Voz", "M1 · español" if supertonic else "Aiden · español · CustomVoice")
        self._line(frame, left_w, 841, "WAV", f"{tm.get('sample_rate', '—')} Hz · {tm.get('duration_s', '—')} s")
        self._line(frame, left_w, 864, "Modelo", "Supertonic 3 ONNX CPU · persistente" if supertonic
                   else "Qwen3-TTS mixto INT4/INT8 · persistente")

        resources = status.get("resources") or {}
        ram = resources.get("ram_available_bytes")
        gpu = resources.get("gpu_free_bytes")
        swap = resources.get("swap_free_bytes")
        footer_y = self.height - 54
        self._section(frame, left_w, footer_y, "RECURSOS EN VIVO")
        self._line(frame, left_w, footer_y + 27, "RAM disponible", self._gib(ram))
        self._line(frame, left_w + 252, footer_y + 27, "VRAM libre", self._gib(gpu))
        self._line(frame, left_w + 480, footer_y + 27, "Swap libre", self._gib(swap))
        if status.get("error"):
            self._text(frame, f"ERROR · {status['error'][:100]}", (left_w + 18, self.height - 13), (82, 102, 236), 0.36)
        return frame

    def show(self, image) -> int:
        self.cv2.imshow(self.window_name, image)
        return self.cv2.waitKey(1) & 0xFF

    def visible(self) -> bool:
        try:
            return self.cv2.getWindowProperty(self.window_name, self.cv2.WND_PROP_VISIBLE) >= 1
        except Exception:
            return False

    def close(self) -> None:
        self.cv2.destroyAllWindows()

    def _fps(self) -> float:
        now = time.monotonic()
        self.fps_times.append(now)
        self.fps_times = self.fps_times[-90:]
        if len(self.fps_times) < 2 or self.fps_times[-1] <= self.fps_times[0]:
            return 0.0
        return (len(self.fps_times) - 1) / (self.fps_times[-1] - self.fps_times[0])

    def _section(self, frame, left: int, y: int, label: str) -> None:
        self.cv2.line(frame, (left + 18, y - 10), (self.width - 16, y - 10), (74, 82, 92), 1)
        self._text(frame, label, (left + 18, y), (86, 205, 124), 0.43, 1)

    def _line(self, frame, left: int, y: int, label: str, value: Any) -> None:
        self._text(frame, f"{label}: {str(value)[:100]}", (left + 18, y), (238, 241, 245), 0.40)

    def _wrapped(self, frame, text: str, x: int, y: int, width: int, line_h: int,
                 scale: float, max_lines: int) -> None:
        chars = max(18, int(width / (scale * 8.7)))
        lines = textwrap.wrap(str(text), width=chars)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][:max(0, chars - 1)] + "…"
        for i, line in enumerate(lines):
            self._text(frame, line, (x, y + i * line_h), (238, 241, 245), scale)

    def _text(self, frame, text: str, point: tuple[int, int], color, scale: float, thickness: int = 1) -> None:
        self.cv2.putText(frame, str(text), point, self.cv2.FONT_HERSHEY_SIMPLEX,
                         scale, color, thickness, self.cv2.LINE_AA)

    @staticmethod
    def _gib(value) -> str:
        return f"{value / (1024**3):.2f} GiB" if isinstance(value, (int, float)) else "—"
