import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:mailer/mailer.dart';
import 'package:mailer/smtp_server.dart';

import 'package:club_n_app/core/email_config.dart';
import 'package:club_n_app/core/models.dart';

class SentEmail {
  final String to;
  final String subject;
  final String body;
  final String qrAssetPath;
  final DateTime sentAt;

  SentEmail({
    required this.to,
    required this.subject,
    required this.body,
    required this.qrAssetPath,
    required this.sentAt,
  });
}

abstract class EmailService extends ChangeNotifier {
  bool get isDemo;
  List<SentEmail> get outbox;
  Future<void> sendGuestCredential(Guest guest);
}

String _guestCredentialBody(Guest guest) =>
    'Hola ${guest.firstName},\n\n'
    'Te hemos registrado como invitado con '
    '${guest.docType.label} ${guest.docNumber}.\n\n'
    'Presenta el código QR adjunto en recepción para acceder al club.\n\n'
    'Saludos,\n'
    'Club N';

const String _guestCredentialSubject = 'Tu carnet de invitado – Club N';
const String _guestQrAssetPath = 'assets/qr/qr_guest.png';

class DemoEmailService extends EmailService {
  final List<SentEmail> _outbox = [];

  @override
  bool get isDemo => true;

  @override
  List<SentEmail> get outbox => List.unmodifiable(_outbox);

  @override
  Future<void> sendGuestCredential(Guest guest) async {
    final email = SentEmail(
      to: guest.email,
      subject: _guestCredentialSubject,
      body: _guestCredentialBody(guest),
      qrAssetPath: _guestQrAssetPath,
      sentAt: DateTime.now(),
    );
    _outbox.add(email);
    notifyListeners();
  }
}

class SmtpEmailService extends EmailService {
  final List<SentEmail> _outbox = [];

  @override
  bool get isDemo => false;

  @override
  List<SentEmail> get outbox => List.unmodifiable(_outbox);

  @override
  Future<void> sendGuestCredential(Guest guest) async {
    const subject = _guestCredentialSubject;
    final body = _guestCredentialBody(guest);
    String actualBody = body;

    try {
      final ByteData qrData = await rootBundle.load(_guestQrAssetPath);
      final Uint8List qrBytes = qrData.buffer.asUint8List();

      final tempFile = File(
        '${Directory.systemTemp.path}/carnet_invitado_clubn.png',
      );
      await tempFile.writeAsBytes(qrBytes, flush: true);

      final message = Message()
        ..from = const Address(EmailConfig.fromAddress, EmailConfig.fromName)
        ..recipients.add(guest.email)
        ..subject = subject
        ..text = body
        ..attachments.add(
          FileAttachment(tempFile, fileName: 'carnet_invitado_clubn.png'),
        );

      await send(
        message,
        SmtpServer(
          EmailConfig.smtpHost,
          port: EmailConfig.smtpPort,
          username: EmailConfig.username,
          password: EmailConfig.password,
          ssl: true,
        ),
      );
    } catch (e) {
      actualBody = '$body\n\n[Nota: el envío falló - $e]';
    }

    _outbox.add(SentEmail(
      to: guest.email,
      subject: subject,
      body: actualBody,
      qrAssetPath: _guestQrAssetPath,
      sentAt: DateTime.now(),
    ));
    notifyListeners();
  }
}

EmailService createEmailService() =>
    EmailConfig.isConfigured ? SmtpEmailService() : DemoEmailService();
