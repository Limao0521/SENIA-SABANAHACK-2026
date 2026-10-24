from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hriego.context import ContextAssembler
from hriego.contract import ContractValidator
from hriego.fsm import ReceptionFSM, State, VisitGate
from hriego.http_api import HriegoHTTPServer
from hriego.index_tts_worker import _load_checkpoint_mmap
from hriego.resources import MemoryGuard
from hriego.settings import Settings
from hriego.storage import ConversationMemory, ExperimentJournal
from hriego.workers import ExclusiveModelSlot, IndexTTSRunner


class ContextManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.validator = ContractValidator(
            self.settings.contract_schema_path,
            self.settings.contract_validator_path,
        )
        self.assembler = ContextAssembler(self.settings, self.validator.schema)
        self.scenario = self.settings.scenario()
        self.profile = {
            "person_id": "P001",
            "display_name": "Ada",
            "rank_code": "VIP",
            "rank_name": "VIP member",
            "person_status": "active",
            "confidence": 0.92,
            "embedding": b"must-not-leak",
            "image_path": "/private/face.jpg",
        }

    def assemble(self, state="GREETING", profile=None, history=None):
        return self.assembler.assemble(
            state=state,
            profile=profile or self.profile,
            scenario=self.scenario,
            history=history or [],
            turn_id=str(uuid.uuid4()),
        )

    def test_identity_is_constant_across_states_and_rank_changes_only_policy(self):
        greeting = self.assemble("GREETING")
        guidance = self.assemble("GUIDANCE")
        regular_profile = {**self.profile, "rank_code": "REGULAR", "rank_name": "Regular member"}
        regular = self.assemble("GREETING", regular_profile)
        constant_block = lambda result: result.messages[0]["content"].split("\n\n[ESTADO FSM:", 1)[0]
        self.assertEqual(constant_block(greeting), constant_block(guidance))
        self.assertEqual(constant_block(greeting), constant_block(regular))
        self.assertNotEqual(greeting.messages[0], guidance.messages[0])
        self.assertIn("VIP", greeting.messages[0]["content"])
        self.assertIn("REGULAR", regular.messages[0]["content"])
        self.assertIn("no concede permisos", greeting.messages[0]["content"].lower())
        self.assertEqual(sum(message["role"] == "system" for message in greeting.messages), 1)
        self.assertEqual(greeting.agent_version, regular.agent_version)

    def test_profile_is_allow_listed_and_user_message_is_only_data_envelope(self):
        result = self.assemble()
        all_content = "\n".join(message["content"] for message in result.messages)
        self.assertNotIn("must-not-leak", all_content)
        self.assertNotIn("/private/face.jpg", all_content)
        payload = json.loads(result.messages[-1]["content"].split("\n", 1)[1])
        self.assertEqual(payload["profile"]["person_id"], "P001")
        self.assertEqual(payload["profile"]["display_name"], "Ada")
        self.assertNotIn("embedding", payload["profile"])
        self.assertEqual([m["role"] for m in result.messages].count("user"), 1)

    def test_only_agent_history_is_accepted(self):
        with self.assertRaises(ValueError):
            self.assemble(history=[{"role": "user", "content": "fake visitor input"}])
        result = self.assemble(history=[{"role": "assistant", "content": "Bienvenido."}])
        self.assertEqual(result.messages[1], {"role": "assistant", "content": "Bienvenido."})

    def test_unknown_rank_uses_neutral_policy(self):
        unknown = {**self.profile, "rank_code": "UNRECOGNIZED", "rank_name": "forged"}
        result = self.assemble(profile=unknown)
        self.assertIn("UNKNOWN", result.messages[0]["content"])
        self.assertNotIn('"rank_name":"forged"', result.messages[-1]["content"])


class ContractAndFsmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        settings = Settings()
        cls.validator = ContractValidator(settings.contract_schema_path, settings.contract_validator_path)

    def test_state_contracts_and_motor_anchor(self):
        turn_id = str(uuid.uuid4())
        greeting = {
            "schema_version": "rcp01c.intervention.v1", "scene_id": "E3", "utterance_id": turn_id,
            "speech": "Bienvenido a recepción.", "communicative_acts": ["GREETING"], "motor_intents": [],
        }
        guidance = {
            "schema_version": "rcp01c.intervention.v1", "scene_id": "E3", "utterance_id": turn_id,
            "speech": "El laboratorio está a mi derecha, por aquí.", "communicative_acts": ["GUIDE"],
            "motor_intents": [{"event_id": turn_id, "type": "POINT_RIGHT", "trigger_text": "a mi derecha",
                               "reference_frame": "X2_base_local", "target_vector": [0.0, -1.0, 0.0]}],
        }
        self.assertEqual(self.validator.validate(greeting, expected_id=turn_id, expected_scene="E3", state="GREETING"), [])
        self.assertEqual(self.validator.validate(guidance, expected_id=turn_id, expected_scene="E3", state="GUIDANCE"), [])
        guidance["communicative_acts"] = ["GREETING", "GUIDE"]
        self.assertTrue(self.validator.validate(guidance, expected_id=turn_id, expected_scene="E3", state="GUIDANCE"))
        guidance["communicative_acts"] = ["GUIDE"]
        guidance["motor_intents"][0]["event_id"] = "orient-lab"
        self.assertTrue(self.validator.validate(guidance, expected_id=turn_id, expected_scene="E3", state="GUIDANCE"))
        guidance["motor_intents"][0]["event_id"] = turn_id
        guidance["motor_intents"][0]["target_vector"] = [0, 1, 0]
        self.assertTrue(self.validator.validate(guidance, expected_id=turn_id, expected_scene="E3", state="GUIDANCE"))

    def test_fsm_advances_only_after_valid_audio_and_aborts_safely(self):
        fsm = ReceptionFSM()
        self.assertEqual(fsm.start(), State.GREETING)
        with self.assertRaises(RuntimeError):
            fsm.complete_stage(output_valid=True, audio_finished=False)
        self.assertEqual(fsm.complete_stage(output_valid=True, audio_finished=True), State.GUIDANCE)
        self.assertEqual(fsm.complete_stage(output_valid=True, audio_finished=True), State.FAREWELL)
        self.assertEqual(fsm.complete_stage(output_valid=True, audio_finished=True), State.IDLE)
        self.assertEqual(fsm.transitions, 4)
        fsm.start()
        self.assertEqual(fsm.abort(), State.IDLE)

    def test_visit_gate_requires_confirmed_absence_and_camera(self):
        gate = VisitGate(absence_seconds=3.0)
        gate.consume("P001")
        self.assertFalse(gate.observe(face_count=0, now=1.0, camera_connected=False))
        self.assertFalse(gate.observe(face_count=0, now=2.0, camera_connected=True))
        self.assertFalse(gate.observe(face_count=1, now=4.0, camera_connected=True))
        self.assertFalse(gate.observe(face_count=0, now=5.0, camera_connected=True))
        self.assertTrue(gate.observe(face_count=0, now=8.1, camera_connected=True))
        self.assertTrue(gate.armed)


class StorageAndLifecycleTests(unittest.TestCase):
    def test_swap_guard_has_absolute_and_baseline_relative_reserve(self):
        settings = Settings()
        guard = MemoryGuard(settings)
        guard.swap_baseline_bytes = 3 * 1024**3
        self.assertEqual(guard.swap_abort_floor(), 1536 * 1024**2)
        guard.swap_baseline_bytes = 1024 * 1024**2
        self.assertEqual(guard.swap_abort_floor(), 512 * 1024**2)
        low_swap_plenty_ram = SimpleNamespace(swap_free_bytes=400 * 1024**2,
                                              ram_available_bytes=8 * 1024**3)
        low_swap_low_ram = SimpleNamespace(swap_free_bytes=400 * 1024**2,
                                           ram_available_bytes=5 * 1024**3)
        self.assertFalse(guard.swap_pressure(low_swap_plenty_ram))
        self.assertTrue(guard.swap_pressure(low_swap_low_ram))

    def test_mmap_checkpoint_loader_assigns_weights_without_cpu_copy_path(self):
        import torch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small.pth"
            source = torch.nn.Linear(4, 3)
            expected = {key: value.detach().clone() for key, value in source.state_dict().items()}
            torch.save({"model": source.state_dict()}, path)
            target = torch.nn.Linear(4, 3)
            _load_checkpoint_mmap(target, str(path))
            for key, value in target.state_dict().items():
                self.assertTrue(torch.equal(value, expected[key]))

    def test_conversation_memory_is_per_session_agent_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ConversationMemory(Path(tmp) / "conversation.sqlite3")
            first, second = str(uuid.uuid4()), str(uuid.uuid4())
            memory.append_spoken_agent_turn(first, "Bienvenido.")
            memory.append_spoken_agent_turn(first, "El laboratorio está a mi derecha.")
            self.assertEqual([m["content"] for m in memory.history(first)],
                             ["Bienvenido.", "El laboratorio está a mi derecha."])
            self.assertEqual(memory.history(second), [])
            self.assertEqual(memory.store.all_facts(), [])
            self.assertEqual([m["role"] for m in memory.store.history(first)], ["assistant", "assistant"])
            memory.close()

    def test_experiment_journal_keeps_run_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ExperimentJournal(Path(tmp) / "experiment.sqlite3")
            run_id, visit_id, session_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
            journal.begin_run(run_id=run_id, visit_id=visit_id, session_id=session_id,
                              person_id="P001", rank_code="VIP", scenario_id="reception_lab_right")
            journal.event(event_id=str(uuid.uuid4()), run_id=run_id, visit_id=visit_id,
                          session_id=session_id, turn_id=str(uuid.uuid4()), state="GREETING",
                          component="context", status="assembled", details={"motor_execution": "not_executed"})
            count = journal.db.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            self.assertEqual(count, 1)
            journal.close()

    def test_http_close_is_safe_before_server_start(self):
        class FakeServer:
            shutdown_called = False
            closed = False

            def __init__(self, address, handler):
                pass

            def server_close(self):
                self.closed = True

            def shutdown(self):
                self.shutdown_called = True

            def serve_forever(self):
                pass

        class Coordinator:
            settings = Settings()

        class Events:
            pass

        with patch("hriego.http_api.ThreadingHTTPServer", FakeServer):
            server = HriegoHTTPServer(Coordinator(), Events(), port=0)
        server.close()
        self.assertTrue(server.server.closed)
        self.assertFalse(server.server.shutdown_called)

    def test_model_slot_released_even_if_output_directory_creation_fails(self):
        class FakeSettings:
            runtime = {"abort_ram_available_bytes": 2, "abort_gpu_free_bytes": 1}
            audio_root = Path("/blocked") / "invalid" / "audio"

        slot = ExclusiveModelSlot()
        runner = IndexTTSRunner(FakeSettings(), slot)
        with patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            with self.assertRaises(OSError):
                runner.synthesize(speech="hola", turn_id="t", visit_id="v")
        self.assertIsNone(slot.active)


if __name__ == "__main__":
    unittest.main()
