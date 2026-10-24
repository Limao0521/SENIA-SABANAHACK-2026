import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:intl/intl.dart';
import 'package:club_n_app/core/models.dart';
import 'package:club_n_app/core/repositories.dart';
import 'package:club_n_app/core/email_service.dart';
import 'login_screen.dart';

class GuestsPage extends StatefulWidget {
  const GuestsPage({super.key});

  @override
  State<GuestsPage> createState() => _GuestsPageState();
}

class _GuestsPageState extends State<GuestsPage> {
  final _formKey = GlobalKey<FormState>();
  final _docNumberController = TextEditingController();
  final _firstNameController = TextEditingController();
  final _lastNameController = TextEditingController();
  final _emailController = TextEditingController();
  DocType _selectedDocType = DocType.cedulaCiudadania;

  @override
  void dispose() {
    _docNumberController.dispose();
    _firstNameController.dispose();
    _lastNameController.dispose();
    _emailController.dispose();
    super.dispose();
  }

  void _clearForm() {
    _formKey.currentState?.reset();
    _docNumberController.clear();
    _firstNameController.clear();
    _lastNameController.clear();
    _emailController.clear();
    setState(() => _selectedDocType = DocType.cedulaCiudadania);
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final guest = Guest(
      docType: _selectedDocType,
      docNumber: _docNumberController.text.trim(),
      firstName: _firstNameController.text.trim(),
      lastName: _lastNameController.text.trim(),
      email: _emailController.text.trim(),
    );
    final guestRepo = context.read<GuestRepository>();
    final emailService = context.read<EmailService>();
    guestRepo.add(guest);
    await emailService.sendGuestCredential(guest);
    if (!mounted) return;
    _clearForm();
    _showEmailPreview(emailService, guest);
  }

  void _showEmailPreview(EmailService emailService, Guest guest) {
    final lastEmail = emailService.outbox.isNotEmpty
        ? emailService.outbox.last
        : null;
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Text('Correo enviado'),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              if (lastEmail != null) ...[
                _InfoRow(label: 'Para', value: lastEmail.to),
                _InfoRow(label: 'Asunto', value: lastEmail.subject),
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.grey.shade50,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: Colors.grey.shade200),
                  ),
                  child: Text(
                    lastEmail.body,
                    style: const TextStyle(fontSize: 13),
                  ),
                ),
                const SizedBox(height: 16),
                Center(
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: kDeepGreen.withValues(alpha: 0.2)),
                    ),
                    child: Image.asset(
                      'assets/qr/qr_guest.png',
                      width: 160,
                      height: 160,
                      errorBuilder: (_, __, ___) => const SizedBox(
                        width: 160,
                        height: 160,
                        child: Icon(Icons.qr_code_2, size: 120),
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 12),
              ] else ...[
                Text('Para: ${guest.email}'),
                const SizedBox(height: 8),
              ],
              if (emailService.isDemo)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: kGold.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: kGold.withValues(alpha: 0.4)),
                  ),
                  child: Row(
                    children: [
                      Icon(Icons.info_outline,
                          size: 16, color: kDeepGreen.withValues(alpha: 0.7)),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'Modo demo: vista previa del correo. Configura SMTP en lib/core/email_config.dart para envío real.',
                          style: TextStyle(
                            fontSize: 11,
                            color: kDeepGreen.withValues(alpha: 0.8),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cerrar'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final guestRepo = context.watch<GuestRepository>();
    final theme = Theme.of(context);
    final timeFormat = DateFormat('HH:mm');

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Registrar invitado',
                      style: theme.textTheme.titleMedium?.copyWith(
                        color: kDeepGreen,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Se le enviará un correo con su código QR de acceso.',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: Colors.grey.shade600,
                      ),
                    ),
                    const SizedBox(height: 20),
                    DropdownButtonFormField<DocType>(
                      initialValue: _selectedDocType,
                      decoration: const InputDecoration(
                        labelText: 'Tipo de documento',
                      ),
                      items: DocType.values.map((dt) {
                        return DropdownMenuItem<DocType>(
                          value: dt,
                          child: Text(dt.label),
                        );
                      }).toList(),
                      onChanged: (v) {
                        if (v != null) {
                          setState(() => _selectedDocType = v);
                        }
                      },
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: _docNumberController,
                      decoration: const InputDecoration(
                        labelText: 'Número de documento',
                      ),
                      keyboardType: TextInputType.number,
                      validator: (v) =>
                          (v == null || v.trim().isEmpty) ? 'Obligatorio' : null,
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: _firstNameController,
                      decoration: const InputDecoration(
                        labelText: 'Nombres',
                      ),
                      textCapitalization: TextCapitalization.words,
                      validator: (v) =>
                          (v == null || v.trim().isEmpty) ? 'Obligatorio' : null,
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: _lastNameController,
                      decoration: const InputDecoration(
                        labelText: 'Apellidos',
                      ),
                      textCapitalization: TextCapitalization.words,
                      validator: (v) =>
                          (v == null || v.trim().isEmpty) ? 'Obligatorio' : null,
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: _emailController,
                      decoration: const InputDecoration(
                        labelText: 'Correo electrónico',
                      ),
                      keyboardType: TextInputType.emailAddress,
                      validator: (v) {
                        if (v == null || v.trim().isEmpty) {
                          return 'Obligatorio';
                        }
                        if (!v.contains('@') || !v.contains('.')) {
                          return 'Correo inválido';
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: 20),
                    ElevatedButton.icon(
                      onPressed: _submit,
                      icon: const Icon(Icons.send_outlined),
                      label: const Text('Registrar invitado'),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'Invitados registrados (${guestRepo.guests.length})',
            style: theme.textTheme.titleSmall?.copyWith(
              color: kDeepGreen,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 8),
          if (guestRepo.guests.isEmpty)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Center(
                  child: Column(
                    children: [
                      Icon(Icons.inbox_outlined,
                          size: 40, color: Colors.grey.shade400),
                      const SizedBox(height: 8),
                      Text(
                        'Aún no has registrado invitados en esta sesión',
                        style: TextStyle(
                          color: Colors.grey.shade500,
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            )
          else
            ...guestRepo.guests.reversed.map((g) {
              return Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: ListTile(
                  leading: CircleAvatar(
                    backgroundColor: kDeepGreen.withValues(alpha: 0.1),
                    child: Text(
                      g.firstName.isNotEmpty ? g.firstName[0].toUpperCase() : '?',
                      style: const TextStyle(
                        color: kDeepGreen,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  title: Text('${g.firstName} ${g.lastName}'),
                  subtitle: Text(g.email),
                  trailing: Text(
                    timeFormat.format(g.registeredAt),
                    style: TextStyle(
                      color: Colors.grey.shade600,
                      fontSize: 12,
                      fontFamily: 'monospace',
                    ),
                  ),
                ),
              );
            }),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  final String label;
  final String value;
  const _InfoRow({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 64,
            child: Text(
              '$label:',
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: Colors.grey.shade600,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}
