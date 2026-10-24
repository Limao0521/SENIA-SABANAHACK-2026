"""The HRI adapter must preserve the club's deterministic visitor routes."""
import pytest

from x2_ultra.control.poses import ARM_POSES, HRI_POSES
from x2_ultra.show.guide import Pause, Say, WaitForGuest, club_tour
from x2_ultra.show.hri import (GESTURE_POSE, Intervention, category_for_profile,
                               generate_club_script, scenario_for_club)


@pytest.mark.parametrize(("rank", "category", "direction"), [
    ("VIP", "vip", "left"), ("REGULAR", "socio", "left"),
    ("GUEST", "invitado", "left"), ("STAFF", "personal", "right"),
    ("EXTERNAL", "externo", "front"),
])
def test_confirmed_face_maps_to_existing_route(rank, category, direction):
    profile = {"identity_status": "CONFIRMED", "rank_code": rank}
    assert category_for_profile(profile) == category
    scenario = scenario_for_club(category, 2 if direction == "left" else None)
    assert scenario["direction"] == direction
    assert scenario["target_vector"] == {
        "left": [0.0, 1.0, 0.0], "right": [0.0, -1.0, 0.0],
        "front": [1.0, 0.0, 0.0],
    }[direction]
    assert GESTURE_POSE[{"left": "POINT_LEFT", "right": "POINT_RIGHT",
                         "front": "POINT_FRONT"}[direction]] in (ARM_POSES | HRI_POSES)


def test_unknown_face_cannot_choose_route():
    with pytest.raises(ValueError):
        category_for_profile({"identity_status": "CANDIDATE", "rank_code": "VIP"})
    with pytest.raises(ValueError):
        category_for_profile({"identity_status": "CONFIRMED", "rank_code": "UNKNOWN"})


def test_validated_script_replaces_speech_and_skips_fake_recognition():
    script = {
        "GREETING": Intervention("Bienvenida real", "GREETING_VIP", "saludar"),
        "GUIDANCE": Intervention("Ascensores a mi izquierda", "POINT_LEFT", "señalar_izquierda"),
        "FAREWELL": Intervention("Que disfrute su visita", "SOCIAL_HEART", "corazon"),
    }
    guest = object()
    tour = club_tour("ultra", "vip", 2, False, guest, script=script, identified=True)
    assert isinstance(tour.steps[0], WaitForGuest)
    assert not any(isinstance(step, Pause) for step in tour.steps)
    spoken = [step for step in tour.steps if isinstance(step, Say) and step.text]
    assert [step.text for step in spoken] == [
        script["GREETING"].speech, script["GUIDANCE"].speech,
        script["FAREWELL"].speech,
    ]
    assert [step.pose for step in spoken] == ["saludar", "señalar_izquierda", "corazon"]


def test_club_generation_uses_validated_gesture_pipeline(monkeypatch):
    from HRIEGO_gesture import run_visit

    called = []
    outputs = {
        "GREETING": ("Hola, bienvenida a Club N esta tarde.", "GREETING_VIP", "Hola"),
        "GUIDANCE": ("Su restaurante está a mi izquierda, en el piso dos.", "POINT_LEFT", "a mi izquierda"),
        "FAREWELL": ("Fue un placer atenderle, hasta luego y disfrute su visita.", "SOCIAL_HEART", "Fue un placer"),
    }

    def fake_turn(settings, _assembler, *, state, profile, scenario, turn_id):
        called.append((state, profile["rank_code"], scenario["direction"], settings.identity_text))
        speech, kind, trigger = outputs[state]
        return {"errors": [], "parsed": {"speech": speech, "motor_intents": [
            {"type": kind, "trigger_text": trigger, "event_id": turn_id}]}}

    monkeypatch.setattr(run_visit, "llm_turn", fake_turn)
    profile = {"person_id": "p1", "identity_status": "CONFIRMED", "rank_code": "VIP",
               "display_name": "Ana", "rank_name": "Socia"}
    script = generate_club_script(profile, 2)
    assert [item[:3] for item in called] == [
        ("GREETING", "VIP", "left"), ("GUIDANCE", "VIP", "left"),
        ("FAREWELL", "VIP", "left")]
    assert all("Club N" in item[3] for item in called)
    assert script["GUIDANCE"].pose == "señalar_izquierda"
    assert script["FAREWELL"].pose == "corazon"
