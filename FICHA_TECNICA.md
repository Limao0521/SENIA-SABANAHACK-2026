# Ficha técnica · SENIA

## Nombre del proyecto

**SENIA** — sistema de recepción robótica personalizada para el Club N.

## Descripción breve

SENIA integra el robot humanoide **AgiBot X2 Ultra**, la plataforma de guía **D1** y una aplicación
móvil para recibir y orientar a visitantes. El X2 reconoce a la persona, solicita su credencial QR para
confirmar su categoría y adapta la conversación, los gestos y las indicaciones a su perfil. D1 forma
parte de la experiencia de guía propuesta; su control físico aún no está integrado.

## Nivel de desarrollo y qué se desarrolló

**Prototipo funcional**, probado en el X2 Ultra real para reconocimiento facial, lectura y validación de
QR, conversación por voz, trato según categoría, gestos, señalamiento y expresiones en la pantalla.
También se desarrollaron:

- Una app Flutter con carnet QR, consulta de espacios, reservas e invitados en modo demostración.
- Una simulación del X2 y el Club N en **MuJoCo**, utilizada para ensayar la interacción y verificar
  movimientos.
- Un recorrido inmersivo con **Meta Quest 3** para validar el flujo de recepción desde la perspectiva
  del visitante, incluida la confirmación de identidad y la conversación en el escenario simulado.

## Ventajas

1. **Trato diferenciado por categoría.** SENIA ajusta el lenguaje y la interacción al rango del
   visitante. La identificación facial seguida de la confirmación mediante QR ayuda a asociar el trato
   con la persona y la categoría correctas. En el prototipo es una doble comprobación; los QR de
   demostración deben sustituirse por credenciales firmadas antes de usarlos para controlar acceso real.
2. **Lenguaje no verbal.** Gestos, expresiones y señalamiento acompañan la voz para crear una interacción
   más cercana y facilitar las indicaciones.
3. **Procesamiento local.** La conversación y la percepción principales se ejecutan a bordo del X2;
   la recepción no requiere una API de inteligencia artificial ni conexión permanente a internet.

## Desventajas

1. **Latencia local.** El tiempo de respuesta percibido puede estar entre **3 y 9 segundos**, según las
   condiciones de operación reportadas por el equipo. Las [métricas documentadas](docs/06-metricas.md)
   registran aproximadamente 5–6 segundos en la versión final a bordo; el rango completo requiere
   medición uniforme en más sesiones.
2. **Tratamiento de datos personales.** La identificación facial y los registros de visita implican
   gestionar autorización, acceso, conservación y eliminación de datos personales y biométricos en el
   marco del derecho de hábeas data. La [guía de privacidad](docs/07-seguridad-y-privacidad.md) detalla
   los datos que usa el prototipo.

## Lenguajes de programación

- **Python:** percepción, conversación y control del robot.
- **Dart:** aplicación móvil Flutter.
- **Bash:** preparación y despliegue del sistema.

## Herramientas, frameworks y servicios

| Área | Componentes principales |
|---|---|
| Robot y despliegue | AgiBot X2 Ultra, AimDK, ROS 2, Docker |
| Simulación y experiencia inmersiva | MuJoCo, Meta Quest 3, WebXR |
| Visión e identidad | UniFace, OpenCV, zxing-cpp |
| Conversación local | Qwen3 con llama.cpp, faster-whisper, Supertonic |
| Aplicación móvil | Flutter |
| Plataforma de guía propuesta | D1; integración de control pendiente |

## Alcance del prototipo

SENIA demuestra la recepción supervisada de **una persona a la vez**: reconocimiento facial,
confirmación de categoría mediante QR, conversación, información sobre el club, comprobación de
permisos, gestos y señalamiento de rutas. La app muestra el carnet y permite probar reservas e
invitados con datos locales. MuJoCo y Meta Quest 3 permiten ensayar el recorrido completo en un
entorno simulado.

El X2 **no camina acompañando al visitante** en la recepción entregada y **D1 no se controla** desde
SENIA. La app usa cuentas y QR de demostración, guarda reservas e invitados en memoria y no comparte
estos datos en tiempo real con el robot. Estas funciones requieren integración adicional antes de una
operación comercial.

## Presupuesto estimado

**COP 51.750.000** como presupuesto preliminar para adaptar e instalar el prototipo en una sede que
ya dispone del X2 Ultra. Es una **estimación de planeación**, no el costo histórico del proyecto ni
una cotización de proveedores.

| Concepto | Supuesto | Estimación |
|---|---:|---:|
| Desarrollo, adaptación e integración | 400 horas × COP 100.000/hora | COP 40.000.000 |
| Instalación y pruebas en sitio | Partida provisional | COP 5.000.000 |
| Contingencia | 15 % de los dos rubros anteriores | COP 6.750.000 |
| **Total estimado** | | **COP 51.750.000** |

El presupuesto **no incluye** la compra o alquiler del X2 Ultra, la plataforma D1 ni las Meta Quest 3;
tampoco cubre una integración de control de D1, un backend comercial de membresías o los costos de
operación y mantenimiento. Esos rubros deben cotizarse según la sede y el alcance contratado.

Para la arquitectura, el flujo de visita y los resultados de las pruebas, consulte el
[README principal](README.md) y la [documentación del proyecto](docs/01-arquitectura.md).
