"""Escenario del reto 1: dos robots (X2 Ultra-N + X2 EDU) y la pantalla de vídeo."""
import shutil
import time

import mujoco
import numpy as np
import pytest

from x2_ultra import paths
from x2_ultra.robot import omnihand as H

needs_policy = pytest.mark.skipif(not paths.default_policy().exists(), reason="policy 'brazos libres' no instalada")


@pytest.fixture(scope="module")
def stage_model():
    from x2_ultra.show.stage import build_stage_model
    return build_stage_model()


def test_stage_has_both_robots_and_screen(stage_model):
    m = stage_model
    jid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)
    assert jid("ultra/floating_base_joint") >= 0 and jid("edu/floating_base_joint") >= 0
    assert jid("ultra/head_pitch_joint") >= 0 and jid("edu/head_yaw_joint") < 0      # el EDU no tiene cuello
    assert jid("ultra/" + H.HAND_JOINTS[0]) < 0 and jid("edu/" + H.HAND_JOINTS[0]) < 0   # muñeca de serie, sin OmniHand
    assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_TEXTURE, "pantalla") >= 0
    d = mujoco.MjData(m)
    mujoco.mj_resetDataKeyframe(m, d, mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_KEY, "home"))
    mujoco.mj_forward(m, d)
    from x2_ultra.show.venue import PLATFORM_H
    assert all(abs(d.qpos[m.jnt_qposadr[jid(f"{k}/floating_base_joint")] + 2] - PLATFORM_H - 0.65) < 0.05
                              for k in ("ultra", "edu"))


@needs_policy
@pytest.mark.slow
def test_both_robots_walk_turn_and_pose_without_falling():
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.venue import PLATFORM_H
    eng = ShowEngine()
    eng.run_for(2.0)
    start = {k: r.info.base_pos.copy() for k, r in eng.rigs.items()}
    for r in eng.rigs.values():          # la tarima acaba 1,4 m por delante: avanzar ~0,8 m
        r.set_velocity_target(0.5, 0, 0)
    eng.run_for(2.0)
    for k, r in eng.rigs.items():
        assert r.info.base_pos[0] - start[k][0] > 0.5, k
    for wz in (1.0, -1.0):
        yaw0 = {k: r.info.yaw for k, r in eng.rigs.items()}
        for r in eng.rigs.values():
            r.set_velocity_target(0, 0, wz)
        eng.run_for(2.0)
        for k, r in eng.rigs.items():
            assert np.sign(np.angle(np.exp(1j * (r.info.yaw - yaw0[k])))) == np.sign(wz), (k, wz)
    for r in eng.rigs.values():
        r.stop_walking()
    eng.run_for(1.0)
    for r in eng.rigs.values():
        assert r.info.base_pos[2] > PLATFORM_H + 0.5     # sigue de pie encima de la tarima
    for pose in ("saludar", "arriba", "aplaudir"):
        for k in eng.rigs:
            eng.play_pose(k, pose)
        eng.run_for(2.0)
    assert all(r.state == "activo" for r in eng.rigs.values())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg para generar el vídeo de prueba")
def test_screen_plays_video_into_texture(stage_model, tmp_path):
    import subprocess

    from x2_ultra.show.screen import VideoScreen
    video = tmp_path / "prueba.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=25:d=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    scr = VideoScreen(stage_model)
    scr.audio = False
    scr.set_image(np.zeros((10, 10, 3), np.uint8))
    assert scr.poll()                                        # imagen fija: textura cambiada
    scr.load(video)
    assert abs(scr.duration - 1.0) < 0.1
    scr.play()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.6:
        scr.poll()
        time.sleep(0.01)
    assert not scr.playing                                   # terminó
    tex = scr._tex.reshape(scr.height, scr.width, 3)
    center = tex[scr.height // 2, scr.width // 2].astype(int)
    assert center[0] > 200 and center[1] < 60                 # rojo en el centro
    assert tex[scr.height // 2, 5].max() < 20                 # 4:3 en 16:9: bandas negras a los lados
    scr.close()


def _synthetic_clip(path, seconds=2.0, fps=30.0):
    """Clip de LinkCraft mínimo: postura por defecto con las rodillas flexionando y la base desplazándose
    0,1 m/s hacia delante y 0,05 m/s hacia su derecha (Z de LinkCraft)."""
    import json

    from x2_ultra.robot import joints as J
    frames = []
    for k in range(int(seconds * fps) + 1):
        t = k / fps
        joints = {n: J.DEFAULT_POSE[n] for n in J.BODY_JOINTS}
        for n in ("left_knee_joint", "right_knee_joint"):
            joints[n] += 0.2 * np.sin(np.pi * t)
        frames.append({"timestamp": t, "rootPosition": [0.1 * t, 0.677, 0.05 * t], "rootRotation": [0, 0, 0],
                       "joints": joints})
    path.write_text(json.dumps({"fps": fps, "frames": frames}))
    return path


def test_linkcraft_axes_to_mujoco():
    from x2_ultra.tools.linkcraft_dance import to_mujoco_position
    np.testing.assert_allclose(to_mujoco_position(np.array([1.0, 2.0, 3.0])), [1.0, -3.0, 2.0])


@needs_policy
def test_robot_dances_a_clip_from_where_it_stands_and_walks_again(tmp_path):
    from x2_ultra.show.dance import BLEND
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.venue import PLATFORM_H
    clip = _synthetic_clip(tmp_path / "prueba.json")
    eng = ShowEngine()
    eng.run_for(0.5)
    edu, ultra = eng.rigs["edu"], eng.rigs["ultra"]
    start = edu.info.base_pos.copy()
    eng.start_dance("edu", clip, "prueba")
    eng.run_for(BLEND + 2.0)
    assert edu.info.state == "bailando" and ultra.info.state == "activo"     # el otro sigue con física
    moved = edu.info.base_pos - start
    assert abs(moved[0] - 0.2) < 0.03 and abs(moved[1] + 0.1) < 0.03         # Z de LinkCraft = −Y de MuJoCo
    assert abs(edu.info.base_pos[2] - (0.677 + PLATFORM_H)) < 0.03          # altura desde la tarima
    eng.run_for(BLEND + 0.1)
    assert edu.dance is None and edu.info.state == "activo"
    edu.set_velocity_target(0.4, 0, 0)
    x0 = edu.info.base_pos[0]
    eng.run_for(2.0)
    assert edu.state == "activo" and edu.info.base_pos[0] - x0 > 0.3            # vuelve a andar sin caerse


def test_club_lobby_builds_with_the_ultra_at_reception():
    from x2_ultra.show.club import ROBOT_START
    from x2_ultra.show.stage import CLUB, build_stage_model
    m = build_stage_model(CLUB)
    d = mujoco.MjData(m)
    mujoco.mj_resetDataKeyframe(m, d, mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_KEY, "home"))
    mujoco.mj_forward(m, d)
    base = m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "ultra/floating_base_joint")]
    np.testing.assert_allclose(d.qpos[base:base + 2], ROBOT_START[:2], atol=0.05)
    assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "edu/floating_base_joint") < 0
    assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_TEXTURE, "pantalla") >= 0     # pantalla de bienvenida


@needs_policy
@pytest.mark.slow
def test_club_guided_reception_greets_walks_the_corridor_and_names_the_floor():
    from x2_ultra.show.club import ELEVATOR_X, HALL
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.guide import elevator_for
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    rig, doors = eng.rigs["ultra"], eng.props
    eng.run_for(0.5)
    floor = 3
    k = elevator_for(floor)
    eng.start_tour(CLUB.tour("ultra", "vip", floor, come_back=False))      # al VIP lo acompaña el robot
    for _ in range(160):                                   # 80 s como máximo
        eng.run_for(0.5)
        if eng.tour.done:
            break
    assert eng.tour.status == "terminada" and rig.state == "activo"
    x, y = rig.info.base_pos[:2]
    assert abs(x - ELEVATOR_X[k]) < 0.4 and HALL[3] - 2.0 < y < HALL[3]          # delante de su ascensor
    assert abs(rig.info.yaw - np.pi / 2) < 0.25                                   # mirando al ascensor
    assert doors.is_open(eng.data, f"ascensor{k}", eng.model) > 0.9
    said = " ".join(text for _, _, text in eng.speech)
    assert "Bienvenido" in said and f"piso {floor}" in said and "le acompaño personalmente" in said


@needs_policy
@pytest.mark.slow
def test_members_and_guests_are_guided_by_the_d1_while_the_robot_stays_at_the_desk():
    from x2_ultra.show.club import DOG_HOME, ELEVATOR_X, HALL, ROBOT_START
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.guide import elevator_for
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    rig, dog = eng.rigs["ultra"], eng.dog
    eng.run_for(0.5)
    eng.start_tour(CLUB.tour("ultra", "invitado", 5, come_back=False))
    reached = False
    for _ in range(int(80 / 0.5)):
        eng.run_for(0.5)
        if dog.mode == "llegado":
            reached = True
            k = elevator_for(5)
            assert abs(dog.x - ELEVATOR_X[k]) < 0.4 and HALL[3] - 2.0 < dog.y < HALL[3]
            break
    assert reached and eng.tour.done and rig.state == "activo"
    assert np.linalg.norm(rig.info.base_pos[:2] - np.array(ROBOT_START[:2])) < 0.3     # el robot no se movió
    said = " ".join(text for _, _, text in eng.speech)
    assert "perro guía Draco" in said and "siga a Draco" in said and "piso 5" in said
    for _ in range(int(60 / 0.5)):                          # y el perro vuelve solo a tumbarse en su sitio
        eng.run_for(0.5)
        if dog.mode == "tumbado":
            break
    assert dog.mode == "tumbado" and np.linalg.norm(dog.xy - np.array(DOG_HOME[:2])) < 0.2


def test_guest_walks_in_but_not_through_walls_furniture_or_the_robot():
    from x2_ultra.show.club import DESK, ROBOT_START
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    g = eng.guest
    occ = g.occupancy
    assert not occ.free(DESK["center"]) and not occ.free((0.0, 0.0))          # mostrador y pared
    assert occ.free((8.0, 0.0)) and occ.free((2.6, 10.0))                      # vestíbulo y pasillo
    g.cmd[:] = (0.9, 0.0, 0.0)                                                 # entra recto hacia la recepción
    for _ in range(int(14 / 0.02)):
        eng.step_control()
    assert eng.props.is_open(eng.data, "entrada", eng.model) < 0.05 or g.x < 10.0
    robot = np.array(ROBOT_START[:2])
    assert g.x < 9.0                                                           # cruzó la puerta
    assert np.linalg.norm(g.xy - robot) > 0.6 and g.x > DESK["center"][0] + 0.4   # no atraviesa robot ni mostrador


@needs_policy
@pytest.mark.slow
def test_robot_waits_for_the_guest_greets_and_guides_them_to_the_lift():
    import math

    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.guide import WaitForGuest
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    rig, g = eng.rigs["ultra"], eng.guest
    eng.start_tour(CLUB.tour("ultra", "vip", 3, False, g))
    eng.run_for(3.0)
    assert isinstance(eng.tour.steps[eng.tour.index], WaitForGuest) and not eng.speech   # sin huésped, espera

    def walk_to(target, stop):
        d = np.asarray(target) - g.xy
        if np.linalg.norm(d) < stop:
            g.cmd[:] = 0.0
            return
        err = (math.atan2(d[1], d[0]) - g.yaw + math.pi) % (2 * math.pi) - math.pi
        g.cmd[:] = (0.9 if abs(err) < 0.6 else 0.2, 0.0, float(np.clip(3 * err, -1.8, 1.8)))

    start = rig.info.base_pos[:2].copy()
    waited = False
    for _ in range(int(110 / 0.1)):
        if eng.tour.done:
            break
        walk_tour = eng.tour.index == 4                     # el paso Walk hasta el ascensor
        if eng.tour.index == 0:
            walk_to((5.6, 0.4), 0.2)                       # entrar hasta el mostrador
        elif walk_tour and not waited and np.linalg.norm(rig.info.base_pos[:2] - start) > 3.0:
            g.cmd[:] = 0.0                                  # quedarse atrás: el robot debe pararse a esperarlo
            waited = eng.tour.waiting
        else:
            walk_to(rig.info.base_pos[:2], 1.4)            # seguir al robot
        eng.run_for(0.1)
    said = " ".join(text for _, _, text in eng.speech)
    assert eng.tour.status == "terminada" and rig.state == "activo"
    assert "Bienvenido" in said and "piso 3" in said
    assert waited and "Por aquí" in said
    assert np.linalg.norm(g.xy - rig.info.base_pos[:2]) < 3.5                 # llegó con el robot al ascensor


@needs_policy
@pytest.mark.slow
def test_staff_are_pointed_to_their_door_without_leaving_the_desk():
    import math

    from x2_ultra.show.club import LOBBY, ROBOT_START, STAFF_DOOR_X
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.guide import FaceToward, Say
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    rig = eng.rigs["ultra"]
    eng.run_for(0.5)
    eng.start_tour(CLUB.tour("ultra", "personal", None, come_back=False))
    door = math.atan2(LOBBY[2] - ROBOT_START[1], STAFF_DOOR_X - ROBOT_START[0])
    pointed = False
    for _ in range(int(40 / 0.5)):
        eng.run_for(0.5)
        if eng.tour.done:
            break
        i = eng.tour.index
        if i > 0 and isinstance(eng.tour.steps[i - 1], FaceToward) and isinstance(eng.tour.steps[i], Say):
            pointed = pointed or abs((rig.info.yaw - door + math.pi) % (2 * math.pi) - math.pi) < 0.25
    assert eng.tour.status == "terminada" and rig.state == "activo" and pointed
    assert np.linalg.norm(rig.info.base_pos[:2] - np.array(ROBOT_START[:2])) < 0.3      # señala sin moverse
    assert "SOLO PERSONAL" in " ".join(text for _, _, text in eng.speech)


def test_external_workers_are_told_that_club_staff_will_come():
    from x2_ultra.show.guide import SendDog, Walk, club_tour
    tour = club_tour("ultra", "externo", None, come_back=False)
    assert not any(isinstance(s, (Walk, SendDog)) for s in tour.steps)
    said = " ".join(getattr(s, "text", "") for s in tour.steps)
    assert "avisar a personal del club" in said
