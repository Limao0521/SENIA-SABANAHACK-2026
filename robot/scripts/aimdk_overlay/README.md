# Overlay de `aimdk_msgs` con lo que el robot tiene y el AimDK público no

**Para qué.** El robot (lx2501 v1.1.4) tiene interfaces que el AimDK público v1.1.0 no trae, así que el PC no puede ni
crearlas ni leerlas sin este overlay:

| Interfaz | La usa | Para qué |
|---|---|---|
| `McBodyPose` (msg) | `scripts/robot_real.sh andar` (F / R) | cuclillas por `/aima/mc/body_pose`; el changelog del SDK la da por retirada, pero el mando y la app la usan |
| `SetMcMotion` (srv), `McMotion` (msg) | `scripts/robot_real.sh movimientos` | reproducir los bailes de fábrica (tipo MIMIC, cuerpo entero) |
| `GetMcMotions` (srv) | `movimientos` | listar los movimientos del MC (bailes MIMIC y animaciones) |

Sin el overlay todo lo demás funciona igual: `andar` dice "cuclillas no disponibles" y `movimientos` no muestra la
lista de bailes de fábrica.

**Qué hace `scripts/build_aimdk_overlay.sh`.** Copia las 154 definiciones `.msg` y 33 `.srv` del AimDK instalado
(`~/robots/x2_simulation/aimdk/src/aimdk_msgs/prebuilt_x86_64/share/aimdk_msgs`, repartidas por carpetas) a un
paquete `aimdk_msgs` nuevo, añade los `.msg`/`.srv` de esta carpeta (salvo los que el SDK ya traiga), lo compila desde fuente con `colcon` en la imagen
`x2-ultra-sim:humble` y deja el resultado en `~/robots/x2_simulation/aimdk_overlay/install`. El AimDK instalado no se
toca. `scripts/robot_real.sh` monta y carga ese `install/` encima del AimDK si existe; como es un `aimdk_msgs`
completo, todos los demás mensajes siguen disponibles.

```bash
scripts/build_aimdk_overlay.sh           # ≈1 min; una vez por PC (y otra si cambia el AimDK o esta carpeta)
scripts/build_aimdk_overlay.sh --check   # comprobar que existe y trae McBodyPose
```

**Archivos.**

| Archivo | Qué es |
|---|---|
| `McBodyPose.msg`, `McMotion.msg`, `SetMcMotion.srv`, `GetMcMotions.srv` | copias de `/agibot/software/common/share/aimdk_msgs/{msg,srv}/` del robot (20/10/2026), con dos líneas de cabecera |
| `CMakeLists.txt` | genera las interfaces de `msg/*.msg` y `srv/*.srv` (sustituye al del SDK, que usa el paquete precompilado) |

**Compatibilidad comprobada (20/10/2026).** Los tipos de los que dependen (`MessageHeader`, `RequestHeader`,
`ResponseHeader`, `McInputSource`, `CommonResponse`, `CommonTaskResponse`, `CommonState`, `McMotionType`) son
idénticos en el robot y en el SDK. Con el overlay el PC recibe el `McBodyPose` del mando (`rc`), el MC obedece el que
publica `andar`, y `GetMcMotions` responde al PC con la lista completa. ROS 2 Humble no transmite
hash de tipo: basta con que el nombre (`aimdk_msgs/msg/McBodyPose`) y los campos coincidan exactamente.

**Actualizar o añadir una definición** si cambia el firmware del robot (solo lectura, por cable): copiarla aquí
(`ros2 interface show aimdk_msgs/srv/<Nombre>` en el robot para ver de qué tipos depende y comprobar que el SDK los
tiene iguales) y volver a ejecutar el guion, que añade todos los `.msg`/`.srv` de esta carpeta.

```bash
sshpass -f <(sed -n 's/^Password: //p' ~/.local/share/g1_teleop/x2-ssh-access.txt) ssh agi@10.0.1.41 \
  cat /agibot/software/common/share/aimdk_msgs/msg/McBodyPose.msg   # comparar con McBodyPose.msg
```

Si el AimDK público pasa a traer alguna de estas interfaces, el guion usa la del SDK; si las trae todas, no compila
nada.
