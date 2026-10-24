"""Visor VR del club: exportación de la escena, huésped movido por las gafas, estado y saludo del check-in."""
import json

import mujoco
import numpy as np
import pytest

from x2_ultra.show.hri import welcome_text
from x2_ultra.vr.export import _decimate, export_scene


def _tiny_model() -> mujoco.MjModel:
    spec = mujoco.MjSpec()
    tex = spec.add_texture(name="t", type=mujoco.mjtTexture.mjTEXTURE_2D, width=4, height=2, nchannel=3)
    tex.data = bytes(range(24))
    mat = spec.add_material(name="m", texrepeat=[2, 2], texuniform=True)
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = "t"
    spec.add_mesh(name="quad", uservert=[0, -1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1], userface=[0, 1, 2, 0, 2, 3],
                  usertexcoord=[0, 1, 1, 1, 1, 0, 0, 0], inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL)
    wb = spec.worldbody
    G = mujoco.mjtGeom
    wb.add_geom(name="suelo", type=G.mjGEOM_PLANE, size=[0, 0, 0.1], material="m")
    wb.add_geom(name="caja", type=G.mjGEOM_BOX, size=[0.5, 0.3, 0.2], pos=[2, 0, 0.2], material="m")
    wb.add_geom(name="cuadro", type=G.mjGEOM_MESH, meshname="quad", pos=[0, 3, 1], material="m", contype=0,
                conaffinity=0, density=0)
    wb.add_geom(name="oculta", type=G.mjGEOM_BOX, size=[1, 1, 1], group=3)
    body = wb.add_body(name="movil", pos=[0, 0, 1])
    body.add_freejoint()
    for gtype, size in ((G.mjGEOM_SPHERE, [0.1, 0, 0]), (G.mjGEOM_CAPSULE, [0.05, 0.2, 0]),
                        (G.mjGEOM_CYLINDER, [0.05, 0.2, 0]), (G.mjGEOM_ELLIPSOID, [0.1, 0.2, 0.3])):
        body.add_geom(type=gtype, size=size, rgba=[1, 0, 0, 1])
    return spec.compile()


def test_export_writes_static_and_moving_chunks(tmp_path):
    model = _tiny_model()
    data = mujoco.MjData(model)
    scene = export_scene(model, data, tmp_path)
    assert json.loads((tmp_path / "scene.json").read_text())["triangles"] == scene["triangles"]
    blob = (tmp_path / "scene.bin").read_bytes()
    assert scene["body_names"] == ["movil"]
    assert (tmp_path / "tex/0.png").is_file() and scene["textures"] == ["tex/0.png"]
    for chunk in scene["static"] + scene["body_chunks"][0]:
        pos = np.frombuffer(blob, np.float32, chunk["pos"][1], chunk["pos"][0]).reshape(-1, 3)
        idx = np.frombuffer(blob, np.uint32, chunk["idx"][1], chunk["idx"][0])
        assert idx.max() < len(pos)
        assert ("uv" in chunk) == (scene["materials"][chunk["material"]]["texture"] >= 0)
    # el suelo, la caja y el cuadro van al mundo; la caja del grupo 3 (colisión) no se exporta
    static_tris = sum(c["idx"][1] for c in scene["static"]) // 3
    assert static_tris == 2 + 12 + 2


def test_box_faces_point_outwards(tmp_path):
    from x2_ultra.vr.export import _box
    part = _box(np.array([0.5, 0.3, 0.2]), np.ones(2), True)
    p = part.pos[part.idx]
    normal = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    assert np.all(np.einsum("ij,ij->i", normal, p.mean(axis=1)) > 0)


def test_decimate_reduces_a_dense_mesh():
    th, ph = np.meshgrid(np.linspace(0, np.pi, 120), np.linspace(0, 2 * np.pi, 240), indexing="ij")
    vert = np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], -1).reshape(-1, 3) * 0.1
    rows, cols = th.shape
    a = (np.arange(rows - 1)[:, None] * cols + np.arange(cols - 1)[None]).ravel()
    face = np.concatenate([np.stack([a, a + cols, a + cols + 1], 1), np.stack([a, a + cols + 1, a + 1], 1)])
    v, f = _decimate(vert, face, 3000)
    assert len(f) <= 3000 and f.max() < len(v)
    assert np.allclose(np.linalg.norm(v, axis=1).mean(), 0.1, atol=0.01)


def test_guest_follows_the_headset_and_reset_sends_it_back():
    from x2_ultra.show import club
    from x2_ultra.show.stage import CLUB, build_stage_model
    model = build_stage_model(CLUB, 0.002)
    data = mujoco.MjData(model)
    guest = club.make_guest(model, data)
    gen = guest.generation
    guest.follow(guest.x - 0.02, guest.y, 2.0)
    guest.update(data, 0.02)
    assert guest.x == pytest.approx(club.GUEST_START[0] - 0.02) and guest.yaw == 2.0
    assert guest.speed == pytest.approx(1.0) and guest.phase > 0
    guest.cmd[:] = (1.0, 0, 0)             # el teclado no manda mientras mandan las gafas
    guest.update(data, 0.02)
    assert guest.x == pytest.approx(club.GUEST_START[0] - 0.02)
    guest.reset(data)
    assert guest.target is None and guest.generation == gen + 1 and guest.x == club.GUEST_START[0]


def test_server_state_matches_its_layout(tmp_path):
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    from x2_ultra.vr.server import VRServer
    eng = ShowEngine(CLUB)
    srv = VRServer(eng, tmp_path)
    scene = srv._export()
    lay = scene["state_layout"]
    assert lay["doors"] == 10 and lay["robots"] == 1 and lay["bodies"] == len(scene["body_names"])
    occ = scene["guest"]["occupancy"]
    assert np.unpackbits(np.frombuffer(__import__("base64").b64decode(occ["bits"]), np.uint8))[
        :np.prod(occ["shape"])].reshape(occ["shape"]).sum() == eng.guest.occupancy.grid.sum()
    msg = srv._snapshot()
    floats = np.frombuffer(msg[4:], np.float32)
    assert msg[0] == 1 and len(floats) == 5 + 4 * lay["doors"] + 2 * lay["robots"] + 7 * lay["bodies"]
    assert floats[2] == pytest.approx(eng.guest.x)
    ultra = scene["body_names"].index("ultra/pelvis")
    k = 5 + 4 * lay["doors"] + 2 * lay["robots"] + 7 * ultra
    assert np.allclose(floats[k:k + 3], eng.data.xpos[scene["bodies"][ultra]], atol=1e-5)


def test_welcome_uses_the_checked_in_name_and_category():
    vip = {"identity_status": "CONFIRMED", "rank_code": "VIP", "display_name": "Ana Ruiz"}
    assert "Ana Ruiz" in welcome_text(vip) and "honor" in welcome_text(vip)
    assert "Ana" in welcome_text({**vip, "rank_code": "STAFF"}) and "turno" in welcome_text({**vip, "rank_code": "STAFF"})
    assert welcome_text(None) == "Bienvenido a Club N. ¿A dónde se dirige?"
    assert welcome_text({**vip, "identity_status": "TENTATIVE"}) == welcome_text(None)


def test_voice_gate_opens_after_speech_and_silence_or_trigger():
    from x2_ultra.vr.hri_visit import VoiceGate
    loud, quiet = (np.ones(320) * 8000).astype(np.int16).tobytes(), bytes(640)
    gate = VoiceGate()
    gate.feed(loud, False)                 # sin armar no escucha (el robot está hablando)
    gate.arm()
    for _ in range(10):
        gate.feed(loud, False)             # 0,2 s de voz: poco
    for _ in range(60):
        gate.feed(quiet, False)
    assert not gate.opened
    for _ in range(30):
        gate.feed(loud, False)
    for _ in range(45):
        gate.feed(quiet, False)
    assert gate.opened
    gate.arm()
    gate.feed(quiet, True)
    gate.feed(quiet, False)                # pulsar y soltar el gatillo
    assert gate.opened


def test_face_profile_maps_to_its_prerecorded_visit():
    from x2_ultra.vr.hri_visit import UNKNOWN, person_for
    assert person_for({"person_id": "P003"}) == "Sebastian"
    assert person_for({"display_name": "laura"}) == "Laura"
    assert person_for(None) == UNKNOWN and person_for({"person_id": "P999"}) == UNKNOWN


def test_reception_waits_for_the_visitor_before_each_dialogue():
    import time as _time
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    from x2_ultra.vr.hri_visit import VoiceGate, vr_reception
    eng = ShowEngine(CLUB)
    gate, played, cues = VoiceGate(), [], []
    eng.guest.follow(6.8, 0.6, np.pi)
    tour, summary = vr_reception("Sebastian", eng.guest, lambda wav: played.append(wav.name) or _time.monotonic(),
                                 gate, on_wait=cues.append)
    assert [s for s, _, _ in summary["turns"]] == ["GREETING", "GUIDANCE", "DIALOGUE", "DIALOGUE", "DIALOGUE",
                                                   "FAREWELL"]
    eng.start_tour(tour)
    eng.run_for(25.0)
    assert len(played) == 2 and tour.player.holding and cues[-1].startswith("Ábreme")
    assert eng.rigs["ultra"].state == "activo"
    gate.force()
    eng.run_for(3.0)
    assert len(played) == 3 and not tour.player.holding and cues[-1] == ""
    assert tour.player.violations == 0 and eng.rigs["ultra"].state == "activo"


def test_conversational_reception_listens_answers_and_guides_to_the_spoken_destination():
    import time as _time
    from concurrent.futures import Future
    from dataclasses import replace
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    from x2_ultra.vr import conversation_visit as CV

    class FakeVoice:                     # sin Whisper/Qwen/Supertonic: reutiliza un WAV de la visita
        def __init__(self):
            self.replies = [{"heard": "¿A qué hora cierra el gimnasio?", "destination": None, "floor": None,
                             "text": "Cierra a las diez. ¿A dónde desea ir?"},
                            {"heard": "Quiero ir al gimnasio", "destination": "gimnasio", "floor": 3,
                             "text": "Claro, piso 3. Sígame, por favor."}]
            self.said = []

        def _done(self, value):
            f = Future()
            f.set_result(value)
            return f

        def _turn(self, text, emote):
            return replace(self.base, turn_id=f"vivo_{len(self.said)}", speech=text, emote=emote, sonic=None)

        def say(self, text, state="DIALOGUE", emote="NONE", rank="guest"):
            self.said.append(text)
            return self._done(self._turn(text, emote))

        def reply(self, wav, history, category, rank="guest", offer=False):
            r = dict(self.replies.pop(0))
            r["turn"] = self._turn(r["text"], "POINT_LEFT" if r["destination"] else "NONE")
            return self._done(r)

        def confirm(self, wav, question, rank="guest", last=False):
            r = dict(self.replies.pop(0))
            r["turn"] = self._turn(r["text"], "NONE")
            return self._done(r)

    eng = ShowEngine(CLUB)
    voice, listener, played = FakeVoice(), CV.Listener(), []
    eng.guest.follow(6.8, 0.6, np.pi)
    tour, summary = CV.conversational_reception("Sebastian", eng.guest,
                                                lambda wav: played.append(wav.name) or _time.monotonic(),
                                                voice, listener)
    voice.base = tour.converse.plans[0].turn
    eng.start_tour(tour)
    loud, quiet = (np.ones(320) * 8000).astype(np.int16).tobytes(), bytes(640)
    for _ in range(200):
        eng.run_for(0.5)
        if tour.converse.phase == "listening" and listener.armed and not listener.heard:
            for _ in range(30):
                listener.feed(loud, False)
            for _ in range(45):
                listener.feed(quiet, False)
        if summary["destination"]:
            break
    assert summary["destination"] == "gimnasio" and played[0].startswith("ps_sebastian_t00_greeting")
    assert [h["content"] for h in tour.converse.history if h["role"] == "user"] == [
        "¿A qué hora cierra el gimnasio?", "Quiero ir al gimnasio"]
    assert any(isinstance(s, CV.LiveTurns) for s in tour.steps) and "ascensor" in voice.said[-1]
    assert eng.rigs["ultra"].state == "activo"


def _offer_conversation(accept_reply):
    """Sebastian (socio) pide el gimnasio; el robot le ofrece el D1 y él contesta ``accept_reply``."""
    import time as _time
    from concurrent.futures import Future
    from dataclasses import replace

    from x2_ultra.show.conversation import OFFER_QUESTION
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    from x2_ultra.vr import conversation_visit as CV

    class FakeVoice:
        def __init__(self):
            self.replies = [{"heard": "Voy al gimnasio", "destination": "gimnasio", "floor": 3,
                             "text": "El gimnasio está en el piso 3. " + OFFER_QUESTION["perro"]},
                            accept_reply]
            self.said, self.confirmed = [], 0

        def _done(self, value):
            f = Future()
            f.set_result(value)
            return f

        def _turn(self, text, emote):
            return replace(self.base, turn_id=f"vivo_{len(self.said)}", speech=text, emote=emote, sonic=None)

        def say(self, text, state="DIALOGUE", emote="NONE", rank="guest"):
            self.said.append(text)
            return self._done(self._turn(text, emote))

        def reply(self, wav, history, category, rank="guest", offer=False):
            assert offer                                  # al socio se le ofrece el acompañamiento
            r = dict(self.replies.pop(0))
            r["turn"] = self._turn(r["text"], "POINT_LEFT")
            return self._done(r)

        def confirm(self, wav, question, rank="guest", last=False):
            self.confirmed += 1
            r = dict(self.replies.pop(0))
            r["turn"] = self._turn(r["text"], "NONE")
            return self._done(r)

    eng = ShowEngine(CLUB)
    voice, listener = FakeVoice(), CV.Listener()
    eng.guest.follow(6.8, 0.6, np.pi)
    start = eng.rigs["ultra"].info.base_pos[:2].copy()
    tour, summary = CV.conversational_reception("Sebastian", eng.guest, lambda wav: _time.monotonic(), voice,
                                                listener)
    voice.base = tour.converse.plans[0].turn
    eng.start_tour(tour)
    loud, quiet = (np.ones(320) * 8000).astype(np.int16).tobytes(), bytes(640)
    for _ in range(200):
        eng.run_for(0.5)
        if tour.converse.phase == "listening" and listener.armed and not listener.heard:
            for _ in range(30):
                listener.feed(loud, False)
            for _ in range(45):
                listener.feed(quiet, False)
        if summary.get("escort"):
            break
    assert voice.confirmed == 1 and summary["destination"] == "gimnasio"
    moved = np.linalg.norm(eng.rigs["ultra"].info.base_pos[:2] - start)
    return tour, summary, moved


def test_the_robot_offers_the_d1_and_sends_it_when_the_member_accepts():
    from x2_ultra.show.guide import SendDog
    tour, summary, moved = _offer_conversation({"heard": "Sí, por favor", "accept": True,
                                                "text": "Con mucho gusto."})
    assert summary["escort"] == "aceptado" and any(isinstance(s, SendDog) for s in tour.steps)
    assert moved < 0.1                                    # atiende sin dar el paso hacia delante


def test_a_member_who_declines_the_escort_is_only_pointed_the_way():
    from x2_ultra.show.guide import FaceToward, SendDog
    tour, summary, _ = _offer_conversation({"heard": "No, gracias, ya sé llegar", "accept": False,
                                            "text": "De acuerdo, le indico el camino."})
    assert summary["escort"] == "rechazado"
    assert not any(isinstance(s, SendDog) for s in tour.steps) and any(isinstance(s, FaceToward) for s in tour.steps)
