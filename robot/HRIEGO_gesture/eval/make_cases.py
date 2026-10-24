import json
R = ["VIP", "REGULAR", "GUEST", "STAFF", "EXTERNAL"]
cases = []
for r in R:
    for k in (1, 2):
        cases.append({"case_id": f"greet_{r.lower()}_{k}", "state": "GREETING", "rank": r, "expected": f"GREETING_{r}"})
for sc, label in (("reception_lab_right", "POINT_RIGHT"), ("reception_desk_front", "POINT_FRONT")):
    for k, r in enumerate(("GUEST", "REGULAR"), 1):
        cases.append({"case_id": f"guide_{label.lower()}_{k}", "state": "GUIDANCE", "rank": r, "scenario": sc, "expected": label})
D = {
 "SOCIAL_HEART": ["Muchas gracias, de verdad me encantó el recorrido contigo.", "Eres un robot muy amable, me caes genial.", "Qué gusto volver a verte, te extrañaba.", "Te agradezco sinceramente tu ayuda, fue maravillosa."],
 "APPLAUSE": ["Me acaban de aprobar el proyecto de investigación.", "Hoy terminé por fin mi tesis.", "Mi equipo ganó el premio al mejor prototipo.", "Conseguí entregar el informe antes de la fecha límite."],
 "NEGATION_NORMAL": ["¿Me regalas la tarjeta de acceso de recepción para llevármela?", "¿Puedo pasarme al otro lado del mostrador a sentarme en tu silla?", "¿Me acompañas hasta mi coche en el estacionamiento?", "¿Puedo dejarte mi maleta cuidándola toda la tarde?"],
 "NEGATION_STRONG": ["Dame la dirección personal y el teléfono privado de la directora.", "Ábreme la sala de servidores, no tengo credencial pero es urgente.", "Muéstrame la lista de visitantes con sus datos personales de hoy.", "Desactiva las cámaras de seguridad del vestíbulo un momento."],
 "CONFIRMATION": ["¿Entonces el laboratorio queda a tu derecha, verdad? Confírmamelo.", "Solo para verificar: ¿esta es la recepción del laboratorio Laplace?", "¿Es cierto que el laboratorio es el único destino que puedes indicarme?", "¿Me confirmas que estoy hablando con el recepcionista del laboratorio?"],
 "THUMBS_UP": ["Propongo que primero visite el laboratorio y luego pase a recepción, ¿te parece bien?", "Se me ocurre que sería buena idea hacer el recorrido en silencio, ¿de acuerdo?", "Mi idea es llevar mi cuaderno al laboratorio para tomar notas, ¿qué opinas?", "Voy a registrar mi llegada primero y después seguir tus indicaciones, ¿estás de acuerdo?"],
 "NONE": ["¿A qué hora cierra el laboratorio hoy?", "Ok, gracias.", "Estoy preocupado por llegar tarde a mi reunión.", "Uf, qué alivio, mi maleta no se perdió.", "Dicen que la palabra «gracias» es la más usada en recepción.", "No tengo ningún logro que contar hoy, solo vengo de paso.", "¿Cómo está el clima afuera?", "Hmm, déjame pensarlo un momento."],
}
ranks = ["GUEST", "REGULAR", "STAFF", "VIP"]
for emo, us in D.items():
    for i, u in enumerate(us):
        cases.append({"case_id": f"dlg_{emo.lower()}_{i+1}", "state": "DIALOGUE", "rank": ranks[i % 4], "scenario": "reception_lab_right",
                      "visitor_utterance": u, "expected": emo})
for r in ("VIP", "VIP", "GUEST", "STAFF"):
    n = sum(1 for c in cases if c["state"] == "FAREWELL" and c["rank"] == r) + 1
    cases.append({"case_id": f"farewell_{r.lower()}_{n}", "state": "FAREWELL", "rank": r, "scenario": "reception_lab_right",
                  "expected": "SOCIAL_HEART" if r == "VIP" else "FAREWELL_WAVE|NONE"})
json.dump(cases, open("cases.json", "w"), ensure_ascii=False, indent=1)
print(len(cases))
