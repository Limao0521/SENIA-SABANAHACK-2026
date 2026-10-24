// Para envío real, llenar los valores de smtpHost, username y password.
// Si están vacíos, la app usa modo demo (DemoEmailService) y los correos
// solo se registran en la bandeja de salida visible en la UI.
class EmailConfig {
  static const String smtpHost = '';
  static const int smtpPort = 587;
  static const String username = '';
  static const String password = '';
  static const String fromAddress = 'membresias@clubn.co';
  static const String fromName = 'Club N';

  static bool get isConfigured =>
      smtpHost.isNotEmpty && username.isNotEmpty && password.isNotEmpty;
}
