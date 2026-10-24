"""A spoken destination cannot send a visitor onto an unconfigured route."""
from __future__ import annotations

import io
import json

import pytest

from x2_ultra.show.conversation import answer, route_floor
from x2_ultra.show.guide import SendDog, Walk, club_tour


def test_spoken_destination_respects_visitor_category():
    assert route_floor("gimnasio", "invitado") == 5
    assert route_floor("servicio", "externo") is None
    assert route_floor("personal", "personal") is None
    with pytest.raises(ValueError):
        route_floor("servicio", "invitado")
    with pytest.raises(ValueError):
        route_floor("gimnasio", "personal")
    with pytest.raises(ValueError):
        route_floor("piso 99", "vip")


def test_tour_after_conversation_starts_with_the_walk():
    tour = club_tour("ultra", "vip", 5, greeting=False)
    assert isinstance(tour.steps[0], Walk)
    assert "Gimnasio" in tour.label


def test_guests_after_conversation_are_handed_to_the_d1():
    tour = club_tour("ultra", "invitado", 3, greeting=False)
    assert any(isinstance(s, SendDog) for s in tour.steps)
    assert not any(isinstance(s, Walk) and s.escort for s in tour.steps)          # el robot no lo acompaña


def test_qwen_destination_reply_invites_the_visitor_to_follow(monkeypatch):
    response = {"choices": [{"message": {"content": json.dumps({
        "respuesta": "El gimnasio está en el piso 3. ¿Desea ir allí?", "destino": "gimnasio",
    })}}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(response).encode()))
    reply = answer("Quiero ir al gimnasio", [], "vip")
    assert reply.destination == "gimnasio"
    assert reply.text == "El gimnasio está en el piso 3. Sígame, por favor."
    reply = answer("Quiero ir al gimnasio", [], "invitado")                       # a él lo acompaña el D1
    assert reply.text == "El gimnasio está en el piso 3. Por favor, siga a Draco."


def test_information_question_does_not_start_a_route(monkeypatch):
    response = {"choices": [{"message": {"content": json.dumps({
        "respuesta": "El gimnasio está en el piso 3.", "destino": "gimnasio",
    })}}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(response).encode()))
    assert answer("¿Dónde queda el gimnasio?", [], "invitado").destination is None


def test_escort_answer_is_read_as_yes_no_or_unclear():
    from x2_ultra.show.conversation import accepts
    assert accepts("Sí, por favor") and accepts("Claro, gracias, no conozco el club")
    assert accepts("No, gracias") is False and accepts("No hace falta, ya sé llegar") is False
    assert accepts("¿Cómo dice?") is None


def test_with_offer_the_reply_asks_instead_of_saying_follow_me(monkeypatch):
    response = {"choices": [{"message": {"content": json.dumps({
        "respuesta": "El gimnasio está en el piso 3. Sígame, por favor.", "destino": "gimnasio",
    })}}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(response).encode()))
    assert answer("Quiero ir al gimnasio", [], "vip", offer=True).text == (
        "El gimnasio está en el piso 3. ¿Desea que le acompañe?")
    assert answer("Quiero ir al gimnasio", [], "socio", offer=True).text == (
        "El gimnasio está en el piso 3. ¿Desea que nuestro perro guía Draco le acompañe?")
    assert answer("Voy a los vestidores", [], "personal", offer=True).text.endswith("Es por allí.")
