from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .context import ContextAssembler
from .fsm import ReceptionFSM, State, VisitGate
from .workers import AudioPlayer, ExclusiveModelSlot, IndexTTSRunner, QwenRunner

log = logging.getLogger("hriego.coordinator")


class HriCoordinator:
    def __init__(self, settings, contract, memory, journal, event_bus, rag=None) -> None:
        self.settings = settings
        self.contract = contract
        self.memory = memory
        self.journal = journal
        self.event_bus = event_bus
        self.rag = rag
        self.assembler = ContextAssembler(settings, contract.schema)
        self.visit_gate = VisitGate(float(settings.runtime["rearm_absence_seconds"]))
        self.model_slot = ExclusiveModelSlot()
        self._lock = threading.RLock()
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._cancel: threading.Event | None = None
        self._current: dict[str, Any] = {
            "state": State.IDLE.value,
            "status": "idle",
            "run_id": None,
            "visit_id": None,
            "session_id": None,
            "turn_id": None,
            "profile": None,
            "scenario_id": None,
            "policy": None,
            "agent_version": self.assembler.agent_version,
            "state_policy_version": settings.state_config["version"],
            "rank_policy_version": settings.rank_config["version"],
            "retrieval_status": "deshabilitado" if not settings.rag_enabled else "pendiente",
            "transitions": [],
            "speech": "",
            "motor_intent": "NONE",
            "trigger_text": "",
            "validation": "sin salida",
            "qwen_status": "idle",
            "qwen_metrics": {},
            "tts_status": "idle",
            "tts_metrics": {},
            "playback_status": "idle",
            "error": None,
            "camera": {},
            "resources": {},
            "updated_at": time.time(),
        }
        self._perception: dict[str, Any] = {"face_count": 0, "profile": None, "camera_connected": False}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._thread = threading.Thread(target=self._run_queue, name="hriego-fsm", daemon=True)
        self._accepting = True
        self._thread.start()

    def close(self) -> None:
        self._accepting = False
        self._stop.set()
        self.cancel("shutdown")
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=30.0)

    def update_perception(self, observation: dict[str, Any]) -> None:
        now = float(observation.get("timestamp", time.monotonic()))
        face_count = max(0, int(observation.get("face_count", 0)))
        camera_connected = bool(observation.get("camera_connected"))
        profile = self._single_confirmed_profile(observation) if camera_connected else None
        with self._lock:
            self._perception = {"face_count": face_count, "profile": profile,
                                "camera_connected": camera_connected, "timestamp": now,
                                "profile_status": observation.get("profile_status")}
            rearmed = self.visit_gate.observe(face_count=face_count, now=now,
                                              camera_connected=camera_connected)
            self._current["camera"] = {
                "status": "CONNECTED" if camera_connected else "DISCONNECTED",
                "face_count": face_count,
                "profile_service": observation.get("profile_status"),
            }
            if profile:
                self._current["profile"] = profile
                self._current["policy"] = self._policy_name(profile.get("rank_code"))
            else:
                self._current["profile"] = None
                self._current["policy"] = None
            self._current["updated_at"] = time.time()
            active = self._current.get("run_id") is not None and self._current.get("status") == "running"
            cancel_event = self._cancel
            if active and (face_count > 1 or not camera_connected):
                if cancel_event is not None:
                    cancel_event.set()
                    reason = "multiple_faces" if face_count > 1 else "camera_disconnected"
                    self.event_bus.publish("run_cancel_requested", run_id=self._current["run_id"], reason=reason)
        if rearmed:
            self.event_bus.publish("visit_gate_rearmed", at_unix=time.time())
        if profile:
            self._auto_start(profile)

    def _single_confirmed_profile(self, observation: dict[str, Any]) -> dict[str, Any] | None:
        if int(observation.get("face_count", 0)) != 1 or observation.get("profile_status") != "OK":
            return None
        detections = observation.get("observations") or []
        if len(detections) != 1:
            return None
        item = detections[0]
        track = item.get("track") or {}
        person_id = track.get("person_id") if track.get("state") == "CONFIRMED" else None
        if not person_id:
            return None
        card = next((c for c in observation.get("cards", [])
                     if c.get("person_id") == person_id and c.get("identity_status") == "CONFIRMED"), None)
        if not card or not card.get("display_name") or card.get("display_name") == "PROFILE UNAVAILABLE":
            return None
        if card.get("person_status", "active") != "active":
            return None
        score = (item.get("scores") or {}).get("top1_score")
        return {
            "person_id": str(person_id),
            "display_name": str(card["display_name"]),
            "person_status": "active",
            "rank_code": card.get("rank_code"),
            "rank_name": card.get("rank_name"),
            "track_id": int(track.get("track_id", card.get("track_id", -1))),
            "confidence": float(score) if isinstance(score, (int, float)) else None,
            "identity_status": "CONFIRMED",
            "identity_source": "Face UniFace/SQLite",
        }

    def _policy_name(self, rank_code: str | None) -> str:
        if rank_code in self.settings.rank_policies:
            return f"{rank_code} · {self.settings.rank_config['version']}"
        return f"UNKNOWN · {self.settings.rank_config['version']}"

    def _auto_start(self, profile: dict[str, Any]) -> None:
        with self._lock:
            if not self._accepting:
                return
            if not self.visit_gate.armed or self._current.get("status") == "running":
                return
            try:
                self._start_locked(profile, self.settings.default_scenario_id, automatic=True)
            except Exception as exc:
                self.event_bus.publish("auto_start_rejected", reason=type(exc).__name__)

    def start_manual(self, scenario_id: str, idempotency_key: str | None = None) -> tuple[int, dict[str, Any]]:
        scenario = self.settings.scenario(scenario_id)
        key = idempotency_key.strip() if idempotency_key else None
        with self._lock:
            if key and key in self._idempotency:
                return 202, {**self._idempotency[key], "duplicate": True}
            if not self._perception.get("camera_connected"):
                return 409, {"error": "camera_not_connected", "state": self._current["state"]}
            profile = self._perception.get("profile")
            if not profile:
                return 409, {"error": "no_single_confirmed_visitor", "face_count": self._perception.get("face_count", 0)}
            if self._current.get("status") == "running":
                active = self._run_identifiers()
                if self._current.get("profile", {}).get("person_id") == profile["person_id"]:
                    return 202, {**active, "duplicate": True}
                return 409, {"error": "another_run_is_active", **active}
            if not self.visit_gate.armed:
                return 409, {"error": "visitor_already_interacted_until_exit", "person_id": profile["person_id"]}
            response = self._start_locked(profile, scenario["scenario_id"], automatic=False)
            if key:
                self._idempotency[key] = response
            return 202, response

    def _start_locked(self, profile: dict[str, Any], scenario_id: str, *, automatic: bool) -> dict[str, Any]:
        if self._current.get("status") == "running":
            raise RuntimeError("ya existe un ciclo HRI activo")
        if not self.visit_gate.armed:
            raise RuntimeError("la visita requiere salida confirmada antes de rearmar")
        scenario = self.settings.scenario(scenario_id)
        ids = {"run_id": str(uuid.uuid4()), "visit_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4())}
        record = {**ids, "profile": dict(profile), "scenario": scenario, "automatic": automatic}
        self.visit_gate.consume(profile["person_id"])
        self.journal.begin_run(
            run_id=ids["run_id"], visit_id=ids["visit_id"], session_id=ids["session_id"],
            person_id=profile["person_id"], rank_code=profile.get("rank_code"),
            scenario_id=scenario["scenario_id"],
        )
        with self._lock:
            self._cancel = threading.Event()
            self._current.update({
                "state": State.IDLE.value,
                "status": "running",
                **ids,
                "turn_id": None,
                "profile": dict(profile),
                "scenario_id": scenario_id,
                "policy": self._policy_name(profile.get("rank_code")),
                "transitions": [],
                "speech": "",
                "motor_intent": "NONE",
                "trigger_text": "",
                "validation": "pendiente",
                "qwen_status": "idle",
                "qwen_metrics": {},
                "tts_status": "idle",
                "tts_metrics": {},
                "playback_status": "idle",
                "error": None,
                "retrieval_status": "deshabilitado" if not self.settings.rag_enabled else "pendiente",
                "updated_at": time.time(),
            })
            cancel = self._cancel
        try:
            self._queue.put_nowait({**record, "cancel": cancel})
        except queue.Full as exc:
            self.visit_gate.armed = True
            self._current.update({"status": "idle", "state": State.IDLE.value})
            self.journal.finish_run(ids["run_id"], "rejected", "QueueFull")
            raise RuntimeError("la cola HRI ya tiene un ciclo pendiente") from exc
        self.event_bus.publish("run_started", **ids, state=State.IDLE.value,
                               person_id=profile["person_id"], scenario_id=scenario_id,
                               start_mode="automatic" if automatic else "api")
        return {**ids, "state": State.IDLE.value, "status": "accepted", "automatic": automatic}

    def cancel(self, reason: str = "api") -> bool:
        with self._lock:
            event = self._cancel
            active = self._current.get("status") == "running"
            if active and event is not None:
                event.set()
                self._current["status"] = "cancelling"
                self._current["updated_at"] = time.time()
        if active:
            self.event_bus.publish("run_cancel_requested", run_id=self._current.get("run_id"), reason=reason)
        return active

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._current)
            result["profile"] = dict(result["profile"]) if result.get("profile") else None
            result["transitions"] = list(result.get("transitions", []))
            result["resources"] = dict(result.get("resources", {}))
            result["perception"] = {
                "face_count": self._perception.get("face_count", 0),
                "camera_connected": self._perception.get("camera_connected", False),
                "profile_service": self._perception.get("profile_status"),
                "visit_gate_armed": self.visit_gate.armed,
            }
            result["active_model"] = self.model_slot.active
            return result

    def update_component(self, **fields: Any) -> None:
        with self._lock:
            self._current.update(fields)
            self._current["updated_at"] = time.time()

    def _run_identifiers(self) -> dict[str, Any]:
        return {key: self._current.get(key) for key in ("run_id", "visit_id", "session_id", "turn_id", "state", "status")}

    def _run_queue(self) -> None:
        while not self._stop.is_set():
            task = self._queue.get()
            if task is None:
                break
            self._run_visit(task)

    def _run_visit(self, task: dict[str, Any]) -> None:
        run_id = task["run_id"]
        visit_id = task["visit_id"]
        session_id = task["session_id"]
        profile = task["profile"]
        scenario = task["scenario"]
        cancel = task["cancel"]
        fsm = ReceptionFSM()
        status = "completed"
        error_class = None
        qwen = QwenRunner(self.settings, self.model_slot, progress=self._component_progress)
        tts = IndexTTSRunner(self.settings, self.model_slot, progress=self._component_progress)
        player = AudioPlayer(progress=self._component_progress)
        try:
            self._transition(fsm.start(), run_id, visit_id, session_id, None)
            while fsm.state is not State.IDLE:
                self._check_cancel(cancel)
                state = fsm.state.value
                turn_id = str(uuid.uuid4())
                scenario_id = scenario["scenario_id"]
                self.update_component(turn_id=turn_id, state=state, qwen_status="preparing",
                                      tts_status="idle", playback_status="idle", speech="",
                                      motor_intent="NONE", trigger_text="", validation="pendiente")
                documents, retrieval_status = self._retrieve(state, scenario)
                assembled = self.assembler.assemble(
                    state=state, profile=profile, scenario=scenario,
                    history=self.memory.history(session_id), documents=documents,
                    retrieval_status=retrieval_status, turn_id=turn_id,
                )
                self.update_component(agent_version=assembled.agent_version,
                                      state_policy_version=assembled.state_policy_version,
                                      rank_policy_version=assembled.rank_policy_version,
                                      retrieval_status=assembled.retrieval_status)
                self.journal.event(
                    event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
                    turn_id=turn_id, state=state, component="context", status="assembled",
                    details={"prompt_chars": assembled.prompt_chars,
                             "agent_version": assembled.agent_version,
                             "state_policy_version": assembled.state_policy_version,
                             "rank_policy_version": assembled.rank_policy_version,
                             "retrieval_status": assembled.retrieval_status,
                             "documents": len(documents)},
                )
                qwen_started = time.perf_counter()
                llm_output = qwen.generate(messages=assembled.messages, state=state, cancel_event=cancel)
                qwen_ms = (time.perf_counter() - qwen_started) * 1000.0
                raw = llm_output.value.get("raw", "")
                parsed = self._parse_contract(raw)
                errors = self.contract.validate(
                    parsed, expected_id=turn_id, expected_scene=scenario["scene_id"], state=state,
                    max_chars=int(self.settings.runtime["max_output_chars"]),
                ) if parsed is not None else ["Qwen no devolvió un objeto JSON puro"]
                if errors:
                    completion_tokens = llm_output.metrics.get("completion_tokens")
                    token_limit = int(self.settings.runtime["max_new_tokens"])
                    truncated = isinstance(completion_tokens, int) and completion_tokens >= token_limit
                    self.update_component(validation="invalid",
                                          qwen_status="truncated_output" if truncated else "invalid_contract",
                                          qwen_metrics={**llm_output.metrics, "wall_ms": round(qwen_ms, 2)},
                                          speech=raw[:int(self.settings.runtime["max_output_chars"])])
                    self.journal.event(
                        event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
                        turn_id=turn_id, state=state, component="qwen", status="invalid_contract",
                        duration_ms=qwen_ms, details={"errors": errors, "raw_chars": len(raw),
                                                     "truncated_at_token_limit": truncated,
                                                     "metrics": llm_output.metrics},
                    )
                    reason = "salida truncada al alcanzar max_new_tokens" if truncated else "; ".join(errors)
                    raise ValueError("Contrato Qwen inválido: " + reason)
                assert parsed is not None
                self._check_cancel(cancel)
                speech = parsed["speech"].strip()
                intents = parsed["motor_intents"]
                intent_label = intents[0]["type"] if intents else "NONE"
                trigger = intents[0]["trigger_text"] if intents else ""
                self.update_component(speech=speech, motor_intent=intent_label, trigger_text=trigger,
                                      validation="válido; no ejecutado", qwen_status="valid",
                                      qwen_metrics={**llm_output.metrics, "wall_ms": round(qwen_ms, 2)})
                self.journal.event(
                    event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
                    turn_id=turn_id, state=state, component="qwen", status="valid",
                    duration_ms=qwen_ms, details={"response": parsed, "metrics": llm_output.metrics,
                                                 "motor_execution": "not_executed"},
                )

                self._check_cancel(cancel)
                self.update_component(tts_status="loading", playback_status="waiting")
                audio_output = tts.synthesize(speech=speech, turn_id=turn_id, visit_id=visit_id,
                                              cancel_event=cancel)
                tts_metrics = audio_output.metrics
                self.update_component(tts_status="ready", tts_metrics=tts_metrics)
                self.journal.event(
                    event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
                    turn_id=turn_id, state=state, component="indextts", status="wav_valid",
                    duration_ms=float(tts_metrics["synthesis_s"]) * 1000.0,
                    details={k: v for k, v in tts_metrics.items() if k != "audio_path"},
                )
                self._check_cancel(cancel)
                self.update_component(playback_status="playing")
                playback = player.play(Path(audio_output.value["path"]), cancel_event=cancel)
                self.update_component(playback_status="finished")
                self.journal.event(
                    event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
                    turn_id=turn_id, state=state, component="playback", status="finished",
                    duration_ms=float(playback["duration_s"]) * 1000.0,
                    details={"player": playback["player"], "wav_path": tts_metrics["audio_path"],
                             "wav_duration_s": tts_metrics["duration_s"], "motor_execution": "not_executed"},
                )
                # A turn becomes memory only after the corresponding audio ended.
                self.memory.append_spoken_agent_turn(session_id, speech)
                next_state = fsm.complete_stage(output_valid=True, audio_finished=True)
                self._transition(next_state, run_id, visit_id, session_id, turn_id)
            self.event_bus.publish("run_completed", run_id=run_id, visit_id=visit_id,
                                   session_id=session_id, state=State.IDLE.value)
        except Exception as exc:
            status = "aborted"
            error_class = type(exc).__name__
            failed_state = fsm.state.value
            log.exception("HRIEGO run aborted run_id=%s state=%s", run_id, failed_state)
            fsm.abort()
            qwen_status = self._current.get("qwen_status")
            if qwen_status not in {"valid", "invalid_contract", "truncated_output"}:
                qwen_status = "error"
            self.update_component(state=State.IDLE.value, status="failed", error=f"{error_class}: {exc}",
                                  qwen_status=qwen_status,
                                  tts_status="error" if self._current.get("tts_status") != "ready" else self._current.get("tts_status"))
            try:
                self.journal.event(
                    event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
                    turn_id=self._current.get("turn_id"), state=failed_state,
                    component="fsm", status="aborted", details={"error_class": error_class, "message": str(exc)[:500]},
                )
            except Exception:
                log.exception("No se pudo registrar el aborto")
            self.event_bus.publish("run_aborted", run_id=run_id, visit_id=visit_id,
                                   error_class=error_class, message=str(exc)[:300])
        finally:
            try:
                self.journal.finish_run(run_id, status, error_class)
            finally:
                with self._lock:
                    self._cancel = None
                    # Preserve the final result for the dashboard until the next run.
                    if status == "completed":
                        self._current.update({"state": State.IDLE.value, "status": "completed"})
                    self._current["updated_at"] = time.time()

    def _retrieve(self, state: str, scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        if not self.settings.rag_enabled:
            return [], "deshabilitado_por_configuración"
        if self.rag is None or not self.rag.available:
            return [], "no_disponible_sin_inicialización_ni_descarga"
        query = f"{state} recepción institucional destino {scenario['destination_label']} dirección {scenario['direction']}"
        try:
            return self.rag.search(query, k=int(self.settings.runtime["rag_top_k"])), "activo"
        except Exception as exc:
            log.exception("RAG retrieval falló; sigo sin contexto documental")
            return [], f"error_{type(exc).__name__}"

    def _component_progress(self, update: dict[str, Any]) -> None:
        if "resources" in update:
            self.update_component(resources=update["resources"])
        if update.get("component") == "Qwen":
            self.update_component(qwen_status=update.get("status", "working"))
        elif update.get("component") == "IndexTTS":
            self.update_component(tts_status=update.get("status", "working"))
        elif update.get("component") == "Audio":
            self.update_component(playback_status=update.get("status", "playing"))
        self.event_bus.publish("component_progress", **update)

    def _transition(self, state: State, run_id: str, visit_id: str,
                    session_id: str, turn_id: str | None) -> None:
        transition = {"state": state.value, "turn_id": turn_id, "at_unix": time.time()}
        with self._lock:
            transitions = list(self._current.get("transitions", []))
            transitions.append(transition)
            self._current.update({"state": state.value, "transitions": transitions,
                                  "updated_at": time.time()})
        self.journal.event(
            event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id, session_id=session_id,
            turn_id=turn_id, state=state.value, component="fsm", status="transition",
            details={"transition": state.value},
        )
        self.event_bus.publish("state_transition", run_id=run_id, visit_id=visit_id,
                               session_id=session_id, turn_id=turn_id, state=state.value)

    @staticmethod
    def _parse_contract(raw: str) -> dict[str, Any] | None:
        if not isinstance(raw, str):
            return None
        stripped = raw.strip()
        if not stripped.startswith("{"):
            return None
        try:
            value, end = json.JSONDecoder().raw_decode(stripped)
        except json.JSONDecodeError:
            return None
        if stripped[end:].strip() or not isinstance(value, dict):
            return None
        return value

    @staticmethod
    def _check_cancel(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise RuntimeError("ciclo cancelado")
