"""Programa de movimientos del X2 real sin ROS: catálogo, condiciones, respuestas y confirmación de cuerpo entero."""
import time
from types import SimpleNamespace as NS

import pytest

from x2_ultra.real import control as K
from x2_ultra.real import motions as MO
from test_real_control import NullLog, good_obs

RESOURCES = [("linkcraft_resource_onnx_01", "Baile, clasico", "0.0.a"), ("linkcraft_resource_02", "Venia", "0.0.b")]
MIMIC = ["M_CHARLESTON_DANCE", "M_ROLL_FORWARD", "M_LONG_JUMP", "M_HIGH_JUMP", "M_BOXING",
         "linkcraft_resource_onnx_01_0.0.a"]


def test_catalog_marks_full_body_and_hides_jumps_and_rolls():
    sections = dict(MO.catalog(RESOURCES, MIMIC))
    gestures = sections["Gestos (SetMcPresetMotion)"]
    assert len(gestures) == 29 and not any(m.full_body for m in gestures)
    linkcraft = sections["LinkCraft (cargados en el robot)"]
    assert [(m.name, m.full_body) for m in linkcraft] == [("Baile, clasico", True), ("Venia", False)]
    dances = sections["Bailes de fábrica (SetMcMotion)"]
    assert [m.key for m in dances] == ["M_BOXING", "M_CHARLESTON_DANCE"] and all(m.full_body for m in dances)
    assert "Bailes de fábrica (SetMcMotion)" not in dict(MO.catalog(RESOURCES, None))


@pytest.mark.parametrize("kw, word", [({"mode": K.MODE_URS}, "STAND_DEFAULT"), ({"fsm": 5}, "estable"),
                                      ({"body_status": 0}, "de pie"), ({"squat_offset": -0.2}, "cuclillas"),
                                      ({"player_state": 2, "motion": "6001"}, "reproduciendo"),
                                      ({"battery": 20}, "batería"), ({"mc_age_s": 3.0}, "estado del MC")])
def test_ready_problems(kw, word):
    assert MO.ready_problems(good_obs()) == []
    assert any(word in p for p in MO.ready_problems(good_obs(**kw)))


def test_judges_follow_each_service_convention():
    preset = lambda code, state: NS(response=NS(header=NS(code=code), state=NS(value=state), task_id=7))
    assert MO.judge_preset(preset(0, 1))[0] and MO.judge_preset(preset(5, 400))[0]
    assert not MO.judge_preset(preset(5, 2))[0]
    # LinkCraft: status es siempre FAILURE en este firmware; manda el mensaje.
    link = lambda msg: NS(header=NS(message=msg, status=NS(value=2), header=NS(code=0)))
    assert MO.judge_linkcraft(link("Play success")) == (True, "Play success")
    assert MO.judge_linkcraft(link("The robot is playing")) == (False, "The robot is playing")
    mimic = lambda code, status: NS(response=NS(header=NS(code=code), status=NS(value=status), message=""))
    assert MO.judge_mimic(mimic(0, 1))[0] and not MO.judge_mimic(mimic(3, 2))[0]


class FakeMotionsLink:
    source = "sdk_x2_ultra_movimientos_000000"
    registered = False

    def __init__(self):
        self.obs = good_obs()
        self.sent = []

    def observed(self, graph=True):
        return self.obs


def controller():
    link = FakeMotionsLink()
    ctl = MO.MotionsController(link, priority=65, ignore_battery=False, log=NullLog())
    ctl.sections = MO.catalog(RESOURCES, MIMIC)
    ctl.tick(time.monotonic())
    plays = []
    ctl._play = lambda motion: (plays.append(motion), setattr(ctl, "busy", False))
    return link, ctl, plays


def run_threads():
    time.sleep(0.05)


def test_full_body_needs_a_second_enter_on_the_same_motion_within_5s():
    link, ctl, plays = controller()
    dance = dict(ctl.sections)["Bailes de fábrica (SetMcMotion)"][0]
    other = dict(ctl.sections)["Bailes de fábrica (SetMcMotion)"][1]
    ctl.request(dance, now=100.0)
    run_threads()
    assert plays == [] and "CUERPO ENTERO" in ctl.message
    ctl.request(other, now=101.0)                       # otro distinto: vuelve a pedir confirmación
    run_threads()
    assert plays == []
    ctl.request(other, now=107.0)                       # pasado el plazo: también
    run_threads()
    assert plays == []
    ctl.request(other, now=108.0)
    run_threads()
    assert plays == [other]


def test_gesture_plays_at_once_but_never_while_another_motion_plays():
    link, ctl, plays = controller()
    wave = dict(ctl.sections)["Gestos (SetMcPresetMotion)"][0]
    link.obs = good_obs(player_state=2, motion="1011")
    ctl.tick(time.monotonic())
    ctl.request(wave)
    run_threads()
    assert plays == [] and "reproduciendo" in ctl.message
    link.obs = good_obs()
    ctl.tick(time.monotonic())
    ctl.request(wave)
    run_threads()
    assert plays == [wave]


def test_fall_or_safe_state_raises_an_alert():
    link, ctl, _ = controller()
    link.obs = good_obs(body_status=6)
    ctl.tick(time.monotonic())
    assert "ALERTA" in ctl.alert
