# 04 · App Club N

App móvil en **Flutter** para los miembros del club. Es la cara del club en el bolsillo del socio y la
fuente del QR que el robot lee en la recepción. Código en `app/`; su README (`app/README.md`) tiene el
detalle completo.

## Qué hace

| Función | Descripción |
|---|---|
| **Login por rol** | VIP, Regular y Staff (cuentas demo en `app/README.md`) |
| **Carnet digital** | Tarjeta con el QR de la membresía — el que se muestra al robot |
| **Espacios accesibles** | Los 15 espacios del club con la matriz de permisos de cada rol |
| **Reservas** | En los espacios donde el rol tiene acceso |
| **Invitados** | Registro de invitados y envío de su carnet con QR por correo (modo demo con bandeja de salida o SMTP real) |

## Ejecutar

```bash
cd app
flutter pub get
flutter run            # o: flutter run -d <dispositivo>
flutter test           # pruebas de dominio y de interfaz
```

## Estructura

```
app/lib/
├── core/      role.dart · space.dart · access.dart (matriz de permisos) · auth.dart · session.dart
│              models.dart · repositories.dart · email_service.dart · email_config.dart
├── ui/        login · carnet · espacios del club · reservas · invitados
└── main.dart
app/assets/qr/ qr_vip.png · qr_regular.png · qr_staff.png · qr_guest.png
```

## Límites del MVP

Login con cuentas fijas (sin backend), datos en memoria, QR estáticos por rol y validación de formularios
mínima. Ver la sección «Limitaciones del MVP» de `app/README.md` y
[08-limitaciones-y-trabajo-futuro.md](08-limitaciones-y-trabajo-futuro.md).
