import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:club_n_app/core/role.dart';
import 'package:club_n_app/core/session.dart';
import 'login_screen.dart';

class CarnetPage extends StatelessWidget {
  const CarnetPage({super.key});

  @override
  Widget build(BuildContext context) {
    final session = context.watch<SessionController>();
    final user = session.user!;
    final theme = Theme.of(context);

    final isVip = user.role == Role.vip;
    final isStaff = user.role == Role.staff;
    final memberCode =
        'CN-${user.role.shortTag}-${(user.name.hashCode % 9000 + 1000).toString().padLeft(4, '0')}';

    final Color accentColor = isVip ? kGold : kDeepGreen;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          const SizedBox(height: 16),
          // The card
          Container(
            width: double.infinity,
            constraints: const BoxConstraints(maxWidth: 420),
            padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 32),
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: isStaff
                    ? [const Color(0xFF2C3E50), const Color(0xFF1A252F)]
                    : [kDeepGreen, const Color(0xFF0A2A20)],
              ),
              borderRadius: BorderRadius.circular(24),
              boxShadow: [
                BoxShadow(
                  color: kDeepGreen.withValues(alpha: 0.25),
                  blurRadius: 24,
                  offset: const Offset(0, 12),
                ),
              ],
              border: Border.all(
                color: accentColor.withValues(alpha: 0.4),
                width: 1,
              ),
            ),
            child: Column(
              children: [
                // Club name header
                Row(
                  children: [
                    Text(
                      'CLUB N',
                      style: theme.textTheme.titleLarge?.copyWith(
                        color: kGold,
                        fontWeight: FontWeight.w300,
                        letterSpacing: 4,
                      ),
                    ),
                    const Spacer(),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(
                        color: isVip
                            ? kGold
                            : kGold.withValues(alpha: 0.2),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Text(
                        user.role.shortTag,
                        style: TextStyle(
                          color: isVip ? kDeepGreen : kGold,
                          fontSize: 10,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 1.2,
                        ),
                      ),
                    ),
                  ],
                ),
                const Divider(color: Colors.white24, height: 32),
                // Member info
                Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    user.name,
                    style: theme.textTheme.headlineSmall?.copyWith(
                      color: Colors.white,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
                const SizedBox(height: 4),
                Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    memberCode,
                    style: const TextStyle(
                      color: Colors.white60,
                      fontSize: 12,
                      letterSpacing: 1.5,
                      fontFamily: 'monospace',
                    ),
                  ),
                ),
                if (user.email.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      user.email,
                      style: const TextStyle(
                        color: Colors.white54,
                        fontSize: 12,
                      ),
                    ),
                  ),
                ],
                const SizedBox(height: 24),
                // QR Code
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Image.asset(
                    user.role.qrAssetPath,
                    width: 220,
                    height: 220,
                    errorBuilder: (context, error, stack) => const SizedBox(
                      width: 220,
                      height: 220,
                      child: Icon(
                        Icons.qr_code_2,
                        size: 160,
                        color: kDeepGreen,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          // Helper text
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.info_outline,
                  size: 16, color: accentColor.withValues(alpha: 0.8)),
              const SizedBox(width: 6),
              Text(
                'Presenta este código en recepción',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: kDeepGreen,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
