"""Pantalla de vídeo del escenario: decodifica un vídeo con ffmpeg y pinta cada fotograma en la textura ``pantalla``.

Un hilo lee los fotogramas ya escalados a la textura, con bandas negras si no es 16:9 (``imageio_ffmpeg``, el ffmpeg
que trae su paquete), y los deja en una cola corta; ``poll()``, llamado desde el ciclo de la interfaz, toma el que
toca según el reloj de reproducción y lo copia en ``model.tex_data``. Devuelve True cuando la textura cambió: entonces
hay que subirla a la GPU (``MujocoViewport.upload_texture``). El audio, si se pide, lo reproduce ``ffplay`` aparte,
arrancado en la misma posición; para un ensayo basta (no se resincroniza).
"""
from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

import mujoco
import numpy as np

QUEUE_FRAMES = 8

# imageio_ffmpeg avisa cada vez que el tamaño de salida difiere del del vídeo: aquí siempre es así (escalamos).
logging.getLogger("imageio_ffmpeg").setLevel(logging.ERROR)


class VideoScreen:
    def __init__(self, model: mujoco.MjModel, texture: str = "pantalla"):
        self.model = model
        self.texid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TEXTURE, texture)
        if self.texid < 0:
            raise KeyError(f"el modelo no tiene la textura {texture}")
        self.width = int(model.tex_width[self.texid])
        self.height = int(model.tex_height[self.texid])
        adr = int(model.tex_adr[self.texid])
        self._tex = model.tex_data[adr:adr + self.width * self.height * 3]   # vista: escribir aquí cambia el modelo
        self.path: Path | None = None
        self.fps = 30.0
        self.duration = 0.0
        self.loop = False
        self.audio = True
        self.playing = False
        self._offset = 0.0           # posición [s] al último play/seek
        self._t0 = 0.0
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._frames: queue.Queue = queue.Queue(maxsize=QUEUE_FRAMES)
        self._pending: tuple[float, bytes] | None = None
        self._ended = False
        self._audio_proc: subprocess.Popen | None = None
        self._dirty = False

    # ------------------------------------------------------------------ imagen fija
    def set_image(self, rgb: np.ndarray) -> None:
        """Muestra una imagen fija (H×W×3 uint8; se reescala al tamaño de la textura)."""
        rgb = np.asarray(rgb, dtype=np.uint8)[..., :3]
        if rgb.shape[:2] != (self.height, self.width):
            ys = (np.arange(self.height) * rgb.shape[0] / self.height).astype(int)
            xs = (np.arange(self.width) * rgb.shape[1] / self.width).astype(int)
            rgb = rgb[ys][:, xs]
        self._tex[:] = rgb.reshape(-1)
        self._dirty = True

    # ------------------------------------------------------------------ vídeo
    def load(self, path: str | Path) -> None:
        import imageio_ffmpeg
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        nframes, duration = imageio_ffmpeg.count_frames_and_secs(str(path))
        self.path, self.duration = path, float(duration)
        self.fps = nframes / duration if duration > 0 else 30.0
        self.seek(0.0, play=False)

    def position(self) -> float:
        if not self.playing:
            return self._offset
        return self._offset + time.perf_counter() - self._t0

    def play(self) -> None:
        if self.path is None or self.playing:
            return
        if self._ended:
            self.seek(0.0, play=False)
        self.playing = True
        self._t0 = time.perf_counter()
        self._start_audio(self._offset)

    def pause(self) -> None:
        if self.playing:
            self._offset = self.position()
            self.playing = False
            self._stop_audio()

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def seek(self, seconds: float, play: bool | None = None) -> None:
        """Salta a ``seconds`` (reinicia el decodificador ahí). ``play`` None conserva el estado."""
        if self.path is None:
            return
        play = self.playing if play is None else play
        self.pause()
        self._stop_reader()
        self._offset = float(np.clip(seconds, 0.0, max(self.duration - 1e-3, 0.0)))
        self._ended = False
        self._start_reader(self._offset)
        if play:
            self.play()

    def restart(self) -> None:
        self.seek(0.0)

    def close(self) -> None:
        self.pause()
        self._stop_reader()

    def poll(self) -> bool:
        """Copia en la textura el fotograma que toca ahora. True si la textura cambió (hay que subirla)."""
        if self.path is not None and not self._ended:
            t = self.position() + 0.5 / self.fps
            frame = None
            while True:                                # el último fotograma con pts <= t; los anteriores se saltan
                if self._pending is None:
                    try:
                        self._pending = self._frames.get_nowait()
                    except queue.Empty:
                        break
                pts, data = self._pending
                if data is None:                       # fin del vídeo
                    self._pending = None
                    self._end()
                    break
                if pts > t:                            # aún no toca
                    break
                frame, self._pending = data, None
            if frame is not None:
                self._tex[:] = np.frombuffer(frame, np.uint8)
                self._dirty = True
        dirty, self._dirty = self._dirty, False
        return dirty

    def _end(self) -> None:
        if self.loop:
            self.seek(0.0, play=True)
            return
        self._offset = self.duration
        self.playing = False
        self._ended = True
        self._stop_audio()

    # ------------------------------------------------------------------ hilos y procesos
    def _start_reader(self, start: float) -> None:
        self._stop = threading.Event()
        self._frames = queue.Queue(maxsize=QUEUE_FRAMES)
        self._pending = None
        self._reader = threading.Thread(target=self._read, args=(self.path, start, self._stop, self._frames),
                                        name="x2-video", daemon=True)
        self._reader.start()

    def _stop_reader(self) -> None:
        if self._reader is not None:
            self._stop.set()
            self._reader.join(timeout=2.0)
            self._reader = None

    def _read(self, path: Path, start: float, stop: threading.Event, frames: queue.Queue) -> None:
        import imageio_ffmpeg
        w, h = self.width, self.height      # sin deformar: bandas negras si el vídeo no es 16:9
        gen = imageio_ffmpeg.read_frames(
            str(path), input_params=["-ss", f"{start:.3f}"] if start > 0 else [],
            output_params=["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"])
        try:
            next(gen)                                  # metadatos
            for i, data in enumerate(gen):
                item = (start + i / self.fps, data)
                while not stop.is_set():
                    try:
                        frames.put(item, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if stop.is_set():
                    return
            while not stop.is_set():
                try:
                    frames.put((float("inf"), None), timeout=0.1)
                    return
                except queue.Full:
                    pass
        finally:
            gen.close()

    def _start_audio(self, start: float) -> None:
        if not self.audio or self.path is None or shutil.which("ffplay") is None:
            return
        self._audio_proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-ss", f"{start:.3f}", str(self.path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _stop_audio(self) -> None:
        if self._audio_proc is not None:
            self._audio_proc.terminate()
            try:
                self._audio_proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._audio_proc.kill()
            self._audio_proc = None
