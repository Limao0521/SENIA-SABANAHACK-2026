"""Guion del Club N para regenerar las visitas realnames del reto 2 (sustituye al escenario de laboratorio).

Las visitas versionadas en ``assets/hri/HRIEGO_gesture/opt/realnames/visits/<persona>`` se generaron con los
escenarios de laboratorio de ``gesture_context.SCENARIOS`` (laboratorio, «sala de juntas», mostrador) y con las
frases de evaluación de ``run_visits_v2.VISITS`` («Ábreme la sala de servidores…», «hacer el recorrido en
silencio…»). En el club eso suena raro. Este módulo da, con la misma forma, lo que hay que usar en su lugar:

- ``CLUB_SCENARIOS``: un escenario por destino real del club, con la dirección de la ruta de ``show.guide`` vista
  desde el robot en la recepción (ascensores a su izquierda, personal a su derecha, servicio al frente) y los datos
  que el robot puede decir (horarios, servicios) para que las respuestas informativas no se inventen nada.
- ``CLUB_VISITS``: por persona de ``assets/hri/identities.json``, su rango, escenario y las 3 frases del
  visitante con el gesto que se busca en cada una (para cubrir los 6 gestos de diálogo del catálogo).
- ``ESCORT_NOTE``: quién acompaña a cada rango (``x2_ultra.show.guide.ESCORT``): el robot acompaña al VIP, el
  perro guía Draco a socios e invitados, al personal solo se le indica la dirección y al trabajador externo se le
  dice que vendrá personal humano. Se añade a la instrucción del escenario para que GUIDANCE no prometa «sígame»
  a quien no va a acompañar.

Uso en la tubería (igual que ``run_visits_v2.run``, cambiando las tablas)::

    scen_id, utts, intended = CLUB_VISITS[persona]["scenario"], CLUB_VISITS[persona]["utterances"], ...
    scenario = {**CLUB_SCENARIOS[scen_id]}
    scenario["instruction"] += " " + ESCORT_NOTE[CLUB_VISITS[persona]["rank"]]
    profile = {**PROFILES[rank], "display_name": persona}      # saludo con el nombre real

Salida: ``opt/realnames/visits/<persona_minúsculas>/`` con el formato de siempre (visit_log.json,
tts_manifest.jsonl, wav, sonic/, anchor_plan/). La recepción en VR (``x2_ultra.vr.hri_visit``) y el CLI de Luis las
leen tal cual: basta con sustituir esas carpetas en ``assets/hri/HRIEGO_gesture/`` y regenerar el .sha256.
"""
from __future__ import annotations

CLUB_FACTS = ("Datos autorizados del Club N (biblioteca de espacios, src/x2_ultra/hri_runtime/club_kb.json): "
              "restaurantes y cafeterías, gastroworking y biblioteca en el piso 2 (restaurantes de 06:00 a 22:00, "
              "biblioteca de 08:00 a 20:00); centro de convenciones y zona infantil en el piso 3; salas de juntas y "
              "oficinas en el piso 4; gimnasio (05:00 a 22:00, lockers y toallas sin costo) y piscina y spa (06:00 a "
              "21:00) en el piso 5; habitaciones en el piso 6; La Cava, de jueves a sábado de 18:00 a 01:00, en el "
              "piso 1, junto a los ascensores; estacionamientos en el S2. Los contratistas se registran en la mesa "
              "del acceso de servicio. El personal entra por la puerta SOLO PERSONAL. No hay más datos: si te "
              "preguntan otra cosa, dilo con amabilidad.")


def _elevators(floor: int, name: str) -> dict:
    return {
        "scenario_id": f"club_elevators_left_p{floor}", "scene_id": "E3", "destination_id": f"floor_{floor}",
        "destination_label": f"{name}, piso {floor}, por los ascensores", "direction": "left",
        "reference_frame": "X2_base_local", "target_vector": [0.0, 1.0, 0.0],
        "instruction": (f"Los ascensores están a la izquierda del robot que habla (tú dices 'a mi izquierda'); el "
                        f"visitante sube al piso {floor}, {name}. Única ubicación autorizada. {CLUB_FACTS}"),
        "required_cue": "a mi izquierda"}


CLUB_SCENARIOS = {
    "club_elevators_left_p2": _elevators(2, "Restaurante"),
    "club_elevators_left_p3": _elevators(3, "Gimnasio"),
    "club_elevators_left_p5": _elevators(5, "Salones de eventos"),
    "club_staff_right": {
        "scenario_id": "club_staff_right", "scene_id": "E3", "destination_id": "staff_lockers",
        "destination_label": "entrada de personal y vestidores", "direction": "right",
        "reference_frame": "X2_base_local", "target_vector": [0.0, -1.0, 0.0],
        "instruction": ("La puerta SOLO PERSONAL, que lleva a los vestidores, está a la derecha del robot que habla "
                        f"(tú dices 'a mi derecha'). Única ubicación autorizada. {CLUB_FACTS}"),
        "required_cue": "a mi derecha"},
    "club_service_front": {
        "scenario_id": "club_service_front", "scene_id": "E3", "destination_id": "service_access",
        "destination_label": "acceso de servicio y registro de contratistas", "direction": "front",
        "reference_frame": "X2_base_local", "target_vector": [1.0, 0.0, 0.0],
        "instruction": ("El acceso de servicio y la mesa de registro de contratistas están frente al robot que habla "
                        f"(tú dices 'frente a mí'). Única ubicación autorizada. {CLUB_FACTS}"),
        "required_cue": "frente a mí"},
}

ESCORT_NOTE = {
    "VIP": "Tú mismo acompañas al visitante: ofrécele acompañarle personalmente.",
    "REGULAR": "Tú te quedas en recepción: su acompañante es nuestro perro guía robot Draco; no digas 'sígame'.",
    "GUEST": "Tú te quedas en recepción: su acompañante es nuestro perro guía robot Draco; no digas 'sígame'.",
    "STAFF": "No le acompañas: solo le indicas hacia dónde ir.",
    "EXTERNAL": "No le acompañas: le dices que vas a avisar a personal del club para que le atienda.",
}

# persona -> rango, escenario, 3 frases del visitante y el gesto buscado en cada una (documentación + reintentos)
CLUB_VISITS = {
    "Laura": {"rank": "VIP", "scenario": "club_elevators_left_p3",
              "utterances": ["Hoy gané el torneo de tenis del club, estoy feliz.",
                             "Gracias por recibirme siempre con tanta amabilidad.",
                             "¿A qué hora cierra el gimnasio hoy?"],
              "intended": ["APPLAUSE", "SOCIAL_HEART", "NONE"]},
    "Sebastian": {"rank": "REGULAR", "scenario": "club_elevators_left_p3",
                  "utterances": ["Ábreme el casillero de otro socio, dejé mis cosas ahí.",
                                 "¿Te parece bien si mañana vengo a entrenar más temprano?",
                                 "¿Hay toallas en el gimnasio?"],
                  "intended": ["NEGATION_STRONG", "THUMBS_UP", "NONE"]},
    "Mario": {"rank": "GUEST", "scenario": "club_elevators_left_p5",
              "utterances": ["Muchas gracias por la bienvenida, el club es precioso.",
                             "Mi anfitrión acaba de recibir un premio en su empresa.",
                             "¿Dónde puedo dejar mi abrigo?"],
              "intended": ["SOCIAL_HEART", "APPLAUSE", "NONE"]},
    "Luis": {"rank": "STAFF", "scenario": "club_staff_right",
             "utterances": ["¿Me prestas la tarjeta maestra de recepción para mi turno?",
                            "¿Entonces hoy entro por la puerta de personal, verdad?",
                            "¿Cómo está el clima afuera?"],
             "intended": ["NEGATION_NORMAL", "CONFIRMATION", "NONE"]},
    "Josaed": {"rank": "EXTERNAL", "scenario": "club_service_front",
               "utterances": ["Terminamos la instalación del aire acondicionado antes de tiempo.",
                              "Apaga un momento las cámaras del vestíbulo para poder trabajar.",
                              "¿Dónde firmo el registro de contratistas?"],
               "intended": ["APPLAUSE", "NEGATION_STRONG", "NONE"]},
    "Desconocido": {"rank": "GUEST", "scenario": "club_elevators_left_p2",
                    "utterances": ["Gracias, es mi primera vez aquí y me siento muy bienvenido.",
                                   "Hoy celebro mi aniversario y vengo a cenar.",
                                   "¿A qué hora cierra el restaurante?"],
                    "intended": ["SOCIAL_HEART", "APPLAUSE", "NONE"]},
}
