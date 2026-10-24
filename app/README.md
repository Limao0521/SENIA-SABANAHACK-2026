# Club N – App MVP

Aplicación móvil en Flutter para gestionar membresías, accesos, reservas y carnet de invitados del Club N.

## Roles de usuario

La app maneja tres roles principales de membresía:

| Rol | Etiqueta | Descripción |
|-----|----------|-------------|
| `Role.vip` | Membresía VIP | Acceso a la mayoría de espacios, incluyendo habitaciones y zonas restringidas. |
| `Role.regular` | Membresía Regular | Acceso a espacios comunes (restaurantes, bares, gimnasio, piscina, etc.). |
| `Role.staff` | Staff | Personal operativo del club con acceso a áreas administrativas, técnicas y logísticas. |

Cada rol visualiza:
- **Carnet digital** con QR identificador.
- **Espacios accesibles** según la matriz de permisos.
- **Reservas** propias en los espacios donde tiene acceso.
- **Registro de invitados** (solo roles con permisos suficientes).

## Matriz de permisos

| Espacio | VIP | Regular | Guest | Staff | Logística ext. | Mantenimiento ext. |
|---------|:---:|:-------:|:-----:|:-----:|:--------------:|:------------------:|
| Recepción y vestíbulos | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Restaurantes y cafeterías | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Bares y La Cava | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Centro de Convenciones | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Salas de juntas | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ |
| Gastroworking | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Gimnasio y deportes | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |
| Piscina, spa y zonas húmedas | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |
| Biblioteca y galería | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Zona infantil y juvenil | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ |
| Habitaciones de alojamiento | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| Oficinas administrativas | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ |
| Almacenes y logística | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Cuartos técnicos y maquinaria | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ |
| Estacionamientos | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Cómo correr la app

```bash
flutter pub get
flutter run
```

Para un dispositivo específico:

```bash
flutter devices          # listar dispositivos
flutter run -d <id>      # correr en un dispositivo
```

### Cuentas demo (login)

La app usa credenciales hardcoded para el MVP (sin backend). Usa cualquiera de estas cuentas en la pantalla de login:

| Usuario | Contraseña | Rol | Nombre |
|---------|------------|-----|--------|
| `vip@clubn.co` | `vip123` | VIP | Laura Franco |
| `regular@clubn.co` | `regular123` | Regular | Sebastian Ulloa |
| `staff@clubn.co` | `staff123` | Staff | Luis Gonzalez |

El login normaliza el usuario (`trim().toLowerCase()`), así que acepta variaciones de mayúsculas y espacios en blanco.

### Correr los tests

```bash
flutter test
```

## Correos a invitados

La app puede enviar correos electrónicos a invitados con sus credenciales (carnet con QR adjunto).

### Modo Demo (por defecto)

Los correos **no se envían** por SMTP. En su lugar, se registran en una bandeja de salida (`outbox`) visible dentro de la app. Esto permite probar el flujo completo sin configuración de servidor de correo.

### Activar envío real por SMTP

1. Edita `lib/core/email_config.dart`.
2. Llena los siguientes valores:
   - `smtpHost`: servidor SMTP (ej. `smtp.gmail.com`)
   - `smtpPort`: puerto (típicamente `587`)
   - `username`: usuario del servidor SMTP
   - `password`: contraseña o contraseña de aplicación
3. La app detecta automáticamente si hay configuración (`EmailConfig.isConfigured`) y usa `SmtpEmailService` en lugar de `DemoEmailService`.

El correo enviado incluye:
- **Asunto:** "Tu carnet de invitado – Club N"
- **Cuerpo:** saludo personalizado, tipo y número de documento, instrucción de presentar QR en recepción, firma "Club N".
- **Adjunto:** imagen QR del invitado (`carnet_invitado_clubn.png`).

Incluso si el envío SMTP falla, el registro queda en el `outbox` con una nota en el cuerpo indicando el error.

## Estructura del proyecto

```
ClubNApp/
├── assets/
│   └── qr/                     # Imágenes QR (vip, regular, staff, guest)
├── lib/
│   ├── core/                   # Lógica de dominio y servicios
│   │   ├── role.dart           # Enum de roles (vip, regular, staff) + extensiones
│   │   ├── space.dart          # Enum de los 15 espacios del club
│   │   ├── access.dart         # Matriz de permisos + canAccess / reservableSpaces
│   │   ├── models.dart         # Guest, Reservation, DocType
│   │   ├── session.dart        # ClubUser + SessionController (ChangeNotifier)
│   │   ├── auth.dart           # AuthService + ClubAccount + kDemoAccounts
│   │   ├── repositories.dart   # GuestRepository + ReservationRepository
│   │   ├── email_config.dart   # Configuración SMTP
│   │   └── email_service.dart  # EmailService / DemoEmailService / SmtpEmailService
│   ├── ui/                     # Pantallas y widgets (en paralelo)
│   └── main.dart               # Entry point (en paralelo)
├── test/
│   └── core_test.dart          # Tests de lógica de dominio
├── pubspec.yaml
├── analysis_options.yaml
└── README.md
```

## Limitaciones del MVP

- **Login simulado:** las credenciales están hardcodeadas en `kDemoAccounts`. No hay registro de usuarios ni autenticación contra un backend real. El `AuthService` está preparado para reemplazar la validación local por una llamada a API.
- **Persistencia en memoria:** `GuestRepository`, `ReservationRepository` y `SessionController` guardan datos en listas/mapas en memoria. Al cerrar la app se pierde toda la información. Para producción se necesita persistencia local (SQLite, Hive) o remota (Firebase, Supabase).
- **QR estáticos:** los archivos en `assets/qr/` son imágenes estáticas. No representan datos codificados del carnet. Para producción se deberían generar QR dinámicamente.
- **Sin manejo de errores robusto:** los servicios asumen condiciones ideales. Falta manejo granular de errores en red, SMTP y almacenamiento.
- **Sin validación de formulario:** el registro de invitados y reservas no valida campos obligatorios más allá de lo necesario para compilar.
