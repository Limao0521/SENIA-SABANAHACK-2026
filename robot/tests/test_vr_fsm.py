"""Recepción VR con la FSM de Luis: identidad por cara + QR, accesos por rango y quién acompaña en el club 3D."""
import time
from collections import deque
from dataclasses import replace

import numpy as np
import pytest

from x2_ultra.show import spaces
from x2_ultra.show.guide import SendDog, Walk
from x2_ultra.vr import fsm_visit as FV

QR = {"Laura": "IDV2.t6nuic0_EcxSbm81a42yyg", "Sebastian": "IDV2.4sWOLajqsmEkJJFhEPkT4A",
      "Mario": "IDV2.qhHB31v-RrYMqxoXbn1qAA", "Luis": "IDV2.WCwWUPOA0ZUamgraoO4l8A"}


# ------------------------------------------------------------------------------------------- identidad
def test_face_and_its_own_qr_confirm_the_rank():
    for person, rank in (("Laura", "VIP"), ("Sebastian", "REGULAR"), ("Mario", "GUEST"), ("Luis", "STAFF")):
        visitor = FV.resolve_visitor(person, QR[person])
        assert visitor["status"] == "CONFIRMED" and visitor["rank"] == rank and visitor["source"] == "qr"


def test_a_qr_of_someone_else_or_no_qr_is_rejected():
    assert FV.resolve_visitor("Laura", QR["Sebastian"])["reason"] == "QR_OTHER_PERSON"
    assert FV.resolve_visitor("Laura", None)["reason"] == "NO_QR"
    assert FV.resolve_visitor("Laura", "IDV2.AAAAAAAAAAAAAAAAAAAAAA")["status"] == "REJECTED"
    assert FV.resolve_visitor("Desconocido", QR["Laura"])["reason"] == "FACE_UNKNOWN"


def test_the_external_worker_checks_in_with_the_simulated_id_card():
    visitor = FV.resolve_visitor("Josaed", None)
    assert visitor["status"] == "CONFIRMED" and visitor["rank"] == "WORKER" and visitor["source"] == "cedula_sim"


def test_without_qr_the_face_alone_gives_the_rank():
    assert FV.resolve_visitor("Laura", None, require_qr=False)["rank"] == "VIP"
    assert FV.resolve_visitor("Desconocido", None, require_qr=False)["rank"] == "GUEST"


# ------------------------------------------------------------------------------------------- espacios
def test_every_kb_space_has_a_route_in_the_3d_club():
    kb = spaces.kb()
    assert len(kb["espacios"]) == 15
    for space in kb["espacios"]:
        assert spaces.route(space) in ("aqui", "ascensores", "personal", "servicio")
        assert kb["espacios"][space]["direccion"] == spaces.direction(space)
    assert spaces.direction("restaurantes_cafeterias") == "left"            # por el pasillo de los ascensores
    assert [f for f, _ in spaces.directory_rows()] == ["6", "5", "4", "3", "2", "1", "S1", "S2"]


# ------------------------------------------------------------------------------------------- visita
class FakeVoice(FV.FsmVoice):
    """La FSM de verdad, sin LLM (frases de respaldo), sin Whisper ni Supertonic: el visitante «dice» ``utterances``
    y cada frase reutiliza el WAV de un turno grabado."""

    def __init__(self, utterances):
        from x2_ultra.show.hri.data import VisitStore
        self.utterances = deque(utterances)
        self.base = VisitStore().visit_plan("socio")[0]
        self.said = []
        super().__init__(audio_dir=".")

    def _warm(self) -> None:
        pass

    def _transcribe(self, wav: bytes) -> str:
        return self.utterances.popleft() if self.utterances else ""

    def _turn(self, text, state, emote, rank):
        self.said.append((text, emote))
        return replace(self.base, turn_id=f"vivo_{len(self.said)}", speech=text, emote=emote, state=state,
                       sonic=None, duration_s=1.0)


def _visit(person: str, utterances, qr=None, seconds: float = 120.0):
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    voice, listener = FakeVoice(utterances), FV.Listener()
    eng.guest.follow(6.8, 0.6, np.pi)
    start = eng.rigs["ultra"].info.base_pos[:2].copy()
    tour, summary = FV.fsm_reception(person, eng.guest, lambda wav: time.monotonic(), voice, listener,
                                     qr=QR.get(person) if qr is None else qr)
    return _run(eng, tour, summary, voice, listener, start, seconds)


def _run(eng, tour, summary, voice, listener, start, seconds, during=None):
    eng.start_tour(tour)
    loud, quiet = (np.ones(320) * 8000).astype(np.int16).tobytes(), bytes(640)
    for _ in range(int(seconds / 0.5)):
        eng.run_for(0.5)
        if tour.converse.phase == "listening" and listener.armed and not listener.heard:
            for _ in range(30):
                listener.feed(loud, False)
            for _ in range(45):
                listener.feed(quiet, False)
        if during is not None:
            during(tour)
        if tour.converse.phase == "finished" and tour.converse.done:
            break
    moved = float(np.linalg.norm(eng.rigs["ultra"].info.base_pos[:2] - start))
    assert eng.rigs["ultra"].state == "activo" and tour.converse.violations == 0
    return tour, summary, voice, moved


def test_a_member_asks_for_the_restaurant_and_the_d1_takes_them():
    tour, summary, voice, moved = _visit("Sebastian", ["Quiero ir al restaurante", "Sí, por favor"])
    assert summary["category"] == "socio" and summary["escort"] == "perro"
    assert summary["destination"] == "restaurantes_cafeterias"
    assert voice.said[0][1] == "GREETING_REGULAR"
    assert "Draco" in voice.said[1][0] and voice.said[1][0].rstrip().endswith("?")          # se lo ofrece
    assert any(isinstance(s, SendDog) for s in tour.steps)
    assert moved < 0.1                                    # atiende desde su puesto


def test_a_rejected_visitor_is_sent_to_staff_without_a_failed_phrase(capsys):
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    voice, listener = FakeVoice([]), FV.Listener()
    eng.guest.follow(6.8, 0.6, np.pi)
    tour, summary = FV.fsm_reception("Josaed", eng.guest, lambda wav: time.monotonic(), voice, listener,
                                     qr=QR["Sebastian"])
    eng.start_tour(tour)
    for _ in range(240):                               # hasta el final del recorrido, despedida incluida
        eng.run_for(0.5)
        if tour.done:
            break
    assert tour.done and summary["category"] == "rechazado" and summary["escort"] == "llamar"
    assert voice.said[0][0] == FV.REJECTED
    assert "frase en vivo fallida" not in capsys.readouterr().out


def test_alfred_says_who_he_and_draco_are_and_the_visit_goes_on():
    tour, summary, voice, _ = _visit("Sebastian", ["¿Cómo te llamas?", "¿Y cómo se llama el perro?",
                                                   "Quiero ir al restaurante", "Sí, por favor"])
    said = [text for text, _ in voice.said]
    assert said[1] == FV.WHO["robot"] and said[2] == FV.WHO["perro"]
    assert summary["escort"] == "perro" and summary["destination"] == "restaurantes_cafeterias"


def test_only_questions_about_the_hosts_get_the_who_phrase():
    asks = lambda text: next((k for k, pat in FV.ASKS_WHO.items() if pat.search(FV._fold(text))), None)
    assert asks("¿Cómo te llamas?") == "robot" and asks("¿Quién es usted?") == "robot"
    assert asks("¿Cómo se llama el perrito?") == "perro" and asks("¿Draco es su nombre?") == "perro"
    assert asks("¿Cómo se llama el restaurante?") is None and asks("Quiero ir al gimnasio") is None


def test_a_vip_who_declines_is_pointed_the_way_and_said_goodbye():
    tour, summary, voice, _ = _visit("Laura", ["Quiero ir a la piscina", "No, gracias"])
    assert summary["escort"] is None and tour.converse.outcomes[-1] == ("señalar", "piscina_spa_humedas")
    emotes = [e for _, e in voice.said]
    assert emotes[0] == "GREETING_VIP" and "POINT_LEFT" in emotes and emotes[-1] == "SOCIAL_HEART"
    assert not any(isinstance(s, (SendDog, Walk)) for s in tour.steps)


def test_a_guest_asking_for_a_forbidden_space_is_refused_and_can_keep_talking():
    tour, summary, voice, _ = _visit("Mario", ["Quiero ir al gimnasio", "Gracias, adiós"])
    emotes = [e for _, e in voice.said]
    assert "NEGATION_NORMAL" in emotes and emotes[-1] == "FAREWELL_WAVE"
    assert summary["escort"] is None and tour.converse.outcomes[-1][0] == "fin"


def test_staff_is_only_pointed_to_a_staff_area():
    tour, summary, voice, _ = _visit("Luis", ["Voy a las oficinas"])
    assert tour.converse.outcomes[-1] == ("señalar", "oficinas_administrativas")
    assert "POINT_LEFT" in [e for _, e in voice.said]
    assert not any(isinstance(s, (SendDog, Walk)) for s in tour.steps)


@pytest.mark.parametrize("person, qr", [("Josaed", ""), ("Laura", QR["Sebastian"])])
def test_external_workers_and_rejected_visitors_get_a_human(person, qr):
    tour, summary, voice, _ = _visit(person, [], qr=qr, seconds=120.0)
    assert summary["escort"] == "llamar"
    assert any("personal del club" in text for text, _ in voice.said)
    if person == "Laura":                                 # rechazada: no se la saluda como VIP
        assert summary["category"] == "rechazado" and voice.said[0][1] == "NONE"


def test_an_offer_for_another_space_than_the_one_asked_is_rewritten():
    class WrongLLM:                                       # el LLM ofrece el gastroworking cuando se pidió el restaurante
        def respond(self, state, rank, person, **kw):
            if state == "CONVERSATION":
                obj = {"type": "NONE", "speech": "Claro que sí, Sebastian.", "trigger_text": "",
                       "intent": "PIDE_DESTINO", "area": "restaurantes_cafeterias"}
            elif state == "GUIDANCE_OFFER":
                obj = {"type": "NONE", "speech": "Sebastian, el gastroworking está libre; ¿desea que Draco le guíe?",
                       "trigger_text": ""}
            else:
                raise RuntimeError("sin LLM: frase de respaldo")
            return {"obj": obj, "errors": []}

    voice = FakeVoice(["Quiero ir al restaurante"])
    voice.llm = WrongLLM()
    voice._begin(FV.resolve_visitor("Sebastian", QR["Sebastian"]))
    reply = voice._hear(b"")
    offer = reply.turns[-1].speech
    assert "restaurantes" in offer and "gastroworking" not in offer and offer.endswith("Draco le acompañe?")


def test_the_robot_asks_for_the_qr_and_waits_for_the_webcam_to_read_it():
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import CLUB
    eng = ShowEngine(CLUB)
    voice, listener, shown = FakeVoice(["Gracias, adiós"]), FV.Listener(), {"qr": None}
    eng.guest.follow(6.8, 0.6, np.pi)
    start = eng.rigs["ultra"].info.base_pos[:2].copy()
    tour, summary = FV.fsm_reception("Sebastian", eng.guest, lambda wav: time.monotonic(), voice, listener,
                                     qr=lambda: shown["qr"])

    def show_qr_when_asked(tour):
        if tour.converse.phase == "qr":
            shown["qr"] = QR["Sebastian"]

    _run(eng, tour, summary, voice, listener, start, 120.0, during=show_qr_when_asked)
    assert voice.said[0][0].startswith("Hola, Sebastian, soy Alfred. Para recibirle") and "QR" in voice.said[0][0]
    assert voice.said[1][1] == "GREETING_REGULAR" and summary["category"] == "socio"
    assert summary["identity"]["source"] == "qr"


def test_without_any_qr_the_robot_reminds_and_finally_calls_staff():
    tour, summary, voice, _ = _visit("Laura", [], qr="", seconds=120.0)
    said = [t for t, _ in voice.said]
    assert said[0].startswith("Hola, Laura") and said[1] == FV.REMIND_QR and said[2] == FV.REJECTED
    assert summary["category"] == "rechazado" and summary["identity"]["reason"] == "NO_QR"
    assert summary["escort"] == "llamar"


def test_asking_about_a_space_gets_that_space_not_the_list_of_all():
    voice = FakeVoice(["Cuéntame del restaurante"])
    voice._begin(FV.resolve_visitor("Sebastian", QR["Sebastian"]))
    answer = voice._hear(b"").turns[-1].speech
    assert "piso 2" in answer and "Restaurantes y cafeterías" in answer and "gimnasio" not in answer.lower()


def test_a_natural_yes_to_the_d1_is_understood_and_an_unclear_one_is_asked_again():
    voice = FakeVoice(["Quiero ir al restaurante", "Sí, que me guíe el perrito"])
    voice._begin(FV.resolve_visitor("Sebastian", QR["Sebastian"]))
    voice._hear(b"")
    assert voice._hear(b"").outcome == "perro"
    voice = FakeVoice(["Quiero ir al restaurante", "Mmm, déjeme pensarlo", "Vale"])
    voice._begin(FV.resolve_visitor("Sebastian", QR["Sebastian"]))
    voice._hear(b"")
    again = voice._hear(b"")
    assert again.outcome is None and again.turns[-1].speech.endswith("Draco le acompañe?")
    assert voice._hear(b"").outcome == "perro"


def test_a_general_question_is_not_answered_with_the_space_the_llm_guessed():
    class GuessingLLM:                                    # el LLM pone la recepción como área de «¿qué hay?»
        def respond(self, state, rank, person, **kw):
            if state != "CONVERSATION":
                raise RuntimeError("sin LLM: frase de respaldo")
            return {"obj": {"type": "NONE", "speech": "Claro que sí, con mucho gusto le cuento.", "trigger_text": "",
                            "intent": "PREGUNTA", "area": "recepcion_vestibulos"}, "errors": []}

    voice = FakeVoice(["Hola, qué hay disponible en el club"])
    voice.llm = GuessingLLM()
    voice._begin(FV.resolve_visitor("Mario", QR["Mario"]))
    answer = voice._hear(b"").turns[-1].speech
    assert "puede visitar" in answer and "Mostrador principal" not in answer


# ------------------------------------------------------------------------------------------- identidad del club
EXCLUDING = ("no está autorizado", "no esta autorizado", "solo para socios", "prohibido", "proceda", "retírese",
             "negativo")
BELONGING = ("nuestro", "nuestra", "su casa", "su club", "parte del equipo", "lugar de encuentro")


def test_every_rank_gets_the_club_identity_and_belonging_in_its_card():
    from x2_ultra.hri_runtime.personas import RANKS, card_text, identity
    ident = identity()
    assert ident["lema"] == "Club N, nuestro lugar de encuentro." and len(ident["valores"]) == 5
    for rank in RANKS:
        card = card_text(rank, "Ana")
        assert "[IDENTIDAD DEL CLUB N]" in card and ident["lema"] in card
        assert ident["pertenencia"][rank] in card                  # misma pertenencia, formalidad distinta


def test_greetings_and_farewells_of_every_rank_make_the_visitor_belong():
    voice = FakeVoice([])
    for person in ("Laura", "Sebastian", "Mario", "Luis", "Josaed"):
        voice._begin(FV.resolve_visitor(person, QR.get(person)))
        greeting = voice.said[-1][0]
        farewell = voice._farewell().turns[-1].speech
        for text in (greeting, farewell):
            assert any(mark in text.lower() for mark in BELONGING), text
            assert not any(word in text.lower() for word in EXCLUDING), text


def test_the_fixed_phrases_never_exclude():
    from x2_ultra.hri_runtime.fsm import _CANNED
    from x2_ultra.show.guide import escort_steps
    llamar = [s.text for s in escort_steps("externo", None, come_back=False)[0] if getattr(s, "text", "")]
    for text in list(_CANNED.values()) + [FV.REJECTED, FV.ASK_QR, FV.REMIND_QR, FV.RETRY_QR] + llamar:
        assert not any(word in text.lower() for word in EXCLUDING), text
