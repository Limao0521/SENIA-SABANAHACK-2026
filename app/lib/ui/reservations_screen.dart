import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:intl/intl.dart';
import 'package:club_n_app/core/space.dart';
import 'package:club_n_app/core/access.dart';
import 'package:club_n_app/core/models.dart';
import 'package:club_n_app/core/session.dart';
import 'package:club_n_app/core/repositories.dart';
import 'login_screen.dart';

class ReservationsPage extends StatefulWidget {
  const ReservationsPage({super.key});

  @override
  State<ReservationsPage> createState() => _ReservationsPageState();
}

class _ReservationsPageState extends State<ReservationsPage> {
  final _formKey = GlobalKey<FormState>();
  ClubSpace? _selectedSpace;
  TimeOfDay? _startTime;
  TimeOfDay? _endTime;
  String? _generalError;

  Future<void> _pickTime({required bool isStart}) async {
    final initial = isStart ? _startTime : _endTime;
    final picked = await showTimePicker(
      context: context,
      initialTime: initial ?? TimeOfDay.now(),
    );
    if (!mounted) return;
    if (picked != null) {
      setState(() {
        if (isStart) {
          _startTime = picked;
        } else {
          _endTime = picked;
        }
        _generalError = null;
      });
    }
  }

  String _formatTime(TimeOfDay? t) {
    if (t == null) return '--:--';
    final h = t.hour.toString().padLeft(2, '0');
    final m = t.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }

  void _submit() {
    setState(() => _generalError = null);
    if (_selectedSpace == null) {
      setState(() => _generalError = 'Selecciona un espacio');
      return;
    }
    if (_startTime == null || _endTime == null) {
      setState(() => _generalError = 'Selecciona la hora de inicio y fin');
      return;
    }
    final now = DateTime.now();
    final start = DateTime(now.year, now.month, now.day,
        _startTime!.hour, _startTime!.minute);
    final end = DateTime(now.year, now.month, now.day,
        _endTime!.hour, _endTime!.minute);

    if (!end.isAfter(start)) {
      setState(() => _generalError = 'La hora de fin debe ser posterior a la de inicio');
      return;
    }

    final repo = context.read<ReservationRepository>();
    if (repo.hasOverlap(_selectedSpace!, start, end)) {
      setState(() => _generalError = 'Ya existe una reserva en este espacio en ese horario');
      return;
    }

    final user = context.read<SessionController>().user!;
    final reservation = Reservation(
      space: _selectedSpace!,
      start: start,
      end: end,
      holderName: user.name,
      holderRole: user.role,
    );
    repo.add(reservation);

    setState(() {
      _selectedSpace = null;
      _startTime = null;
      _endTime = null;
    });

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Reserva creada'),
        backgroundColor: kDeepGreen,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<SessionController>();
    final repo = context.watch<ReservationRepository>();
    final user = session.user!;
    final theme = Theme.of(context);
    final timeFormat = DateFormat('HH:mm');

    final allowedSpaces = reservableSpaces(user.role);

    final userReservations = repo.byHolder(user.name);

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
                      'Nueva reserva',
                      style: theme.textTheme.titleMedium?.copyWith(
                        color: kDeepGreen,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Reserva un espacio del club para el día de hoy.',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: Colors.grey.shade600,
                      ),
                    ),
                    const SizedBox(height: 20),
                    DropdownButtonFormField<ClubSpace>(
                      initialValue: _selectedSpace,
                      decoration: const InputDecoration(
                        labelText: 'Espacio',
                      ),
                      items: allowedSpaces.map((space) {
                        return DropdownMenuItem<ClubSpace>(
                          value: space,
                          child: Text(space.displayName),
                        );
                      }).toList(),
                      onChanged: (v) {
                        setState(() {
                          _selectedSpace = v;
                          _generalError = null;
                        });
                      },
                    ),
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        Expanded(
                          child: _TimeSelector(
                            label: 'Hora de inicio',
                            value: _formatTime(_startTime),
                            onTap: () => _pickTime(isStart: true),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: _TimeSelector(
                            label: 'Hora de fin',
                            value: _formatTime(_endTime),
                            onTap: () => _pickTime(isStart: false),
                          ),
                        ),
                      ],
                    ),
                    if (_generalError != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        _generalError!,
                        style: TextStyle(
                          color: theme.colorScheme.error,
                          fontSize: 13,
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    ElevatedButton.icon(
                      onPressed: _submit,
                      icon: const Icon(Icons.event_available),
                      label: const Text('Crear reserva'),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'Mis reservas (${userReservations.length})',
            style: theme.textTheme.titleSmall?.copyWith(
              color: kDeepGreen,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 8),
          if (userReservations.isEmpty)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Center(
                  child: Column(
                    children: [
                      Icon(Icons.event_busy_outlined,
                          size: 40, color: Colors.grey.shade400),
                      const SizedBox(height: 8),
                      Text(
                        'No tienes reservas aún',
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
            ...userReservations.map((r) {
              return Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: ListTile(
                  leading: Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: kDeepGreen.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.event_seat,
                        color: kDeepGreen, size: 20),
                  ),
                  title: Text(r.space.displayName),
                  subtitle: Text(
                    '${timeFormat.format(r.start)} – ${timeFormat.format(r.end)}',
                    style: const TextStyle(fontFamily: 'monospace'),
                  ),
                  trailing: Icon(Icons.check_circle_outline,
                      color: kDeepGreen.withValues(alpha: 0.6)),
                ),
              );
            }),
        ],
      ),
    );
  }
}

class _TimeSelector extends StatelessWidget {
  final String label;
  final String value;
  final VoidCallback onTap;

  const _TimeSelector({
    required this.label,
    required this.value,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          suffixIcon: const Icon(Icons.schedule),
        ),
        child: Text(
          value,
          style: const TextStyle(
            fontSize: 16,
            fontFamily: 'monospace',
            color: kDeepGreen,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}
