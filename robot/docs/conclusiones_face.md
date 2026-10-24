# Conclusiones de la carpeta `Face`

## Resumen

`Face/` contiene un prototipo de reconocimiento facial local para una estación de trabajo con webcam y panel visual. El sistema detecta rostros, compara sus embeddings con una galería SQLite, usa BYTETracker y una política temporal para estabilizar identidades, y consulta un perfil institucional para mostrar nombre y rango.

La implementación está organizada en módulos y tiene controles razonables para una prueba local. Aun así, el estado es de prototipo: no encontré integración activa con ROS, tópicos de cámara del robot ni una conexión con el LLM/RAG. Tampoco hay evidencia suficiente para evaluar la precisión biométrica o considerarlo listo para decisiones de acceso.

## Qué hace

El flujo principal, descrito en `Face/README.md` y el paquete `Face/live/`, es:

1. Recibe imágenes de una webcam local o de un archivo de video y conserva el cuadro más reciente para limitar la latencia.
2. Detecta rostros y genera embeddings con UniFace (RetinaFace MNET_V2 y EdgeFace XXS).
3. Compara cada embedding con la galería activa mediante similitud coseno. Usa umbrales fijos de `0.45` y un margen de `0.08` para separar coincidencias, desconocidos y casos ambiguos.
4. BYTETracker asocia detecciones entre cuadros. `TemporalIdentityManager` exige observaciones repetidas antes de confirmar una identidad y la suspende ante ambigüedad o pérdida del seguimiento.
5. Un servicio separado consulta perfiles confirmados en SQLite y el dashboard presenta el resultado y métricas de ejecución.

El registro de identidad guarda personas, plantillas vectoriales y rangos. Los cambios de rango tienen una tabla de auditoría. El CLI permite listar perfiles, consultarlos y cambiar rangos.

## Aspectos bien resueltos

- **Separación de responsabilidades:** adquisición, reconocimiento, seguimiento, estado temporal, acceso al perfil y renderizado están divididos en módulos distintos.
- **Identidad con confirmación temporal:** una coincidencia de un solo cuadro no basta para mostrar el perfil. Los casos ambiguos o suspendidos no conservan la personalización visible.
- **Lectura separada del perfil:** el reconocimiento mantiene una galería en memoria; el servicio de perfiles consulta SQLite en segundo plano. Si la consulta falla, el perfil aparece como no disponible.
- **Protección básica de archivos locales:** la base biométrica y los informes observados tienen permisos `0600`; el logger configura el directorio de sesión como `0700`. Las reglas de Git excluyen la base, el entorno virtual, los pesos descargados y `outputs/`.
- **Sin guardar imágenes ni embeddings en el logger de sesión:** el código excluye el cuadro de imagen y las plantillas vectoriales del registro.

## Evidencia de ejecución encontrada

Los artefactos existentes corresponden a una ejecución con webcam del experimento `UF-IDREG-01`:

- 438 cuadros recibidos y 427 procesados; el registro indica 388 observaciones faciales y una galería de 5 identidades.
- El servicio de perfiles reportó estado `OK`.
- La tasa registrada fue de aproximadamente 10.2 FPS para cámara e inferencia. La latencia media registrada fue de 11.65 ms para UniFace, 0.33 ms para BYTETracker y 0.45 ms para la consulta del perfil.
- La memoria reportada fue de 1,811.5 MB de RAM y 543 MB de VRAM.
- Esa ejecución terminó con `window_closed_cleanly: false`; además, el receptor registró que la cámara local dejó de entregar cuadros. Por eso prueba que hubo una sesión real con cuadros procesados, pero no demuestra estabilidad prolongada.

Estas cifras describen una sola ejecución y no miden precisión, falsos positivos, falsos negativos ni desempeño con distintas condiciones de luz, distancia o cámaras.

## Límites y riesgos técnicos

### La precisión biométrica no está validada

Los umbrales de coincidencia están fijados en el código. El seguimiento temporal reduce cambios espurios, pero no sustituye una evaluación con datos etiquetados. No encontré métricas de FAR/FRR, matriz de confusión, protocolo de calibración ni pruebas con condiciones variadas. Una galería de cinco identidades tampoco permite concluir cómo se comportará con una población mayor.

### No se ve detección de vida en el flujo activo

El flujo activo construye el detector y el reconocedor; no encontré una etapa de liveness o anti-spoofing conectada a `LiveRecognizer`. Una foto o una reproducción frente a la cámara no parece estar cubierta por la lógica actual. Esto es relevante si el resultado se fuera a usar para autorizar acceso o acciones sensibles.

### El logger guarda metadatos de identidad

Aunque no guarda cuadros ni embeddings, `SessionLogger` conserva datos como candidatos de identidad, nombres, puntuaciones de similitud, cajas delimitadoras, IDs y estados de seguimiento, y marcas de tiempo. Es información personal vinculable a la presencia de una persona. Los permisos de archivo ayudan, pero no encontré una política de retención o borrado automático. El CLI también usa `local-admin` como responsable por defecto al cambiar un rango, lo que debilita la atribución individual del historial.

### La entrada MJPEG no está disponible por la interfaz actual

`FrameReceiver` contiene un lector MJPEG y el programa declara `--url`, pero `parse_args()` solo permite `webcam` o `video`. Por tanto, desde la CLI actual no se puede seleccionar el lector MJPEG. El README además aclara que el proyecto no usa tópicos de cámara del robot. Tampoco hay marca temporal del sensor para sincronizar visión con voz, gestos u otros eventos de HRI.

### No identifica quién está hablando

El sistema resuelve identidad visual por rostro/track. No encontré diarización ni asociación entre una voz activa y una cara. Si se conecta a conversación, la identidad visual no debe tomarse por sí sola como identidad del hablante.

## Conclusión y siguientes pasos

`Face/` es una base local útil para experimentar con reconocimiento facial y perfiles institucionales: el pipeline está modularizado, distingue identidades tentativas de confirmadas y ya tiene evidencia de una ejecución con webcam. Por ahora lo clasificaría como **prototipo funcional de escritorio, todavía no integrado en el robot y sin validación biométrica suficiente para producción**.

Para conectarlo con HRI, los siguientes pasos de mayor valor serían:

1. Añadir y validar la fuente de cámara real que se vaya a usar (MJPEG o ROS) y propagar marcas de tiempo del sensor.
2. Medir precisión y latencia con un protocolo etiquetado y representativo; calibrar umbrales para esa cámara y entorno.
3. Definir retención y borrado de sesiones; minimizar o seudonimizar los candidatos y nombres guardados en logs, y exigir un responsable identificable en cambios administrativos.
4. Incorporar detección de vida si el uso previsto implica acceso o decisiones sensibles.
5. Exponer al diálogo solo perfiles visuales confirmados, con su confianza/estado y procedencia; añadir diarización antes de atribuir una intervención hablada a una persona.

## Archivos revisados

- `Face/README.md`
- `Face/live/main.py`
- `Face/live/live_recognizer.py`
- `Face/live/identity_registry.py`
- `Face/live/profile_service.py`
- `Face/live/temporal_identity.py`
- `Face/live/tracker_adapter.py`
- `Face/live/mjpeg_client.py`
- `Face/src/uniface_runner.py`
- `Face/.gitignore`
- Metadatos agregados de `Face/outputs/UF_IDREG_01/`; no se leyeron nombres, embeddings ni filas de los registros biométricos o de sesión.

No ejecuté pruebas ni alteré archivos de `Face/`.
