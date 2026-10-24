import 'package:flutter/material.dart';
import 'package:club_n_app/core/space.dart';
import 'login_screen.dart';

/// Página de entrada para miembros: identidad, historia e información
/// práctica del Club N.
class ClubInfoPage extends StatelessWidget {
  const ClubInfoPage({super.key});

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 20),
      children: const [
        _HeroHeader(),
        SizedBox(height: 16),
        _HistoryCard(),
        SizedBox(height: 16),
        _SpacesCard(),
        SizedBox(height: 16),
        _ExperiencesCard(),
        SizedBox(height: 16),
        _PracticalCard(),
      ],
    );
  }
}

class _HeroHeader extends StatelessWidget {
  const _HeroHeader();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 20),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [kDeepGreen, kDeepGreen.withValues(alpha: 0.82)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
      ),
      child: const Column(
        children: [
          Text(
            'CLUB N',
            style: TextStyle(
              letterSpacing: 6,
              fontSize: 30,
              fontWeight: FontWeight.w700,
              color: kGold,
            ),
          ),
          SizedBox(height: 6),
          Text(
            'El norte nos une desde 1987',
            style: TextStyle(
              color: Colors.white70,
              fontSize: 14,
              fontStyle: FontStyle.italic,
            ),
          ),
          SizedBox(height: 14),
          Text(
            'SABANA NORTE · BOGOTÁ · COLOMBIA',
            style: TextStyle(
              color: Colors.white,
              fontSize: 12,
              letterSpacing: 1.2,
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoCard extends StatelessWidget {
  const _InfoCard({
    required this.title,
    required this.icon,
    required this.child,
  });

  final String title;
  final IconData icon;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.06),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: kGold, size: 20),
              const SizedBox(width: 8),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                  color: kDeepGreen,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          child,
        ],
      ),
    );
  }
}

class _HistoryCard extends StatelessWidget {
  const _HistoryCard();

  @override
  Widget build(BuildContext context) {
    return const _InfoCard(
      title: 'Nuestra historia',
      icon: Icons.auto_stories_outlined,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Club N nació en 1987, cuando doce familias de la sabana '
            'compraron una antigua hacienda lechera a las afueras de Bogotá '
            'para convertirla en su punto de encuentro. La casa principal '
            'de la hacienda sigue en pie como nuestra sede social, y el '
            'roble de más de cien años que sombrea la plaza central es el '
            'símbolo del club desde el primer día.',
          ),
          SizedBox(height: 10),
          Text(
            'Lo que empezó como asados de fin de semana alrededor de la '
            'parrilla de piedra hoy es una comunidad de más de ochocientas '
            'familias miembros. La "N" de nuestro nombre viene del viento '
            'del norte que recorre los terrenos cada tarde: el mismo que '
            'acompaña a los miembros desde hace casi cuatro décadas.',
          ),
        ],
      ),
    );
  }
}

class _SpacesCard extends StatelessWidget {
  const _SpacesCard();

  @override
  Widget build(BuildContext context) {
    return _InfoCard(
      title: 'Instalaciones',
      icon: Icons.place_outlined,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Quince espacios al servicio de nuestros miembros:'),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final space in kAllSpaces)
                Chip(
                  label: Text(
                    space.displayName,
                    style: const TextStyle(fontSize: 12),
                  ),
                  backgroundColor: kDeepGreen.withValues(alpha: 0.08),
                  side: BorderSide.none,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  visualDensity: VisualDensity.compact,
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ExperiencesCard extends StatelessWidget {
  const _ExperiencesCard();

  @override
  Widget build(BuildContext context) {
    return const _InfoCard(
      title: 'Experiencias',
      icon: Icons.star_outline,
      child: Column(
        children: [
          _ExperienceRow(
            icon: Icons.restaurant_outlined,
            title: 'Gastronomía',
            subtitle: 'Cocina de la sabana en El Establo, cafeterías y La Cava',
          ),
          _ExperienceRow(
            icon: Icons.fitness_center_outlined,
            title: 'Bienestar',
            subtitle:
                'Piscina climatizada, spa, gimnasio y canchas al aire libre',
          ),
          _ExperienceRow(
            icon: Icons.menu_book_outlined,
            title: 'Cultura',
            subtitle:
                'Biblioteca del Roble, galería y conciertos de cámara',
          ),
          _ExperienceRow(
            icon: Icons.business_center_outlined,
            title: 'Negocios',
            subtitle:
                'Convenciones y gastroworking en la antigua lechería',
          ),
          _ExperienceRow(
            icon: Icons.child_care_outlined,
            title: 'Familia',
            subtitle:
                'Club de niños, zona juvenil y doce habitaciones de huéspedes',
          ),
        ],
      ),
    );
  }
}

class _ExperienceRow extends StatelessWidget {
  const _ExperienceRow({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  final IconData icon;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: kDeepGreen.withValues(alpha: 0.7)),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                Text(
                  subtitle,
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _PracticalCard extends StatelessWidget {
  const _PracticalCard();

  @override
  Widget build(BuildContext context) {
    return const _InfoCard(
      title: 'Información práctica',
      icon: Icons.info_outline,
      child: Column(
        children: [
          _InfoRow(
            icon: Icons.schedule_outlined,
            text: 'Abierto todos los días · 6:00 a. m. – 11:00 p. m.',
          ),
          _InfoRow(
            icon: Icons.place_outlined,
            text: 'Km 4, vía Sabana Norte, Bogotá D. C.',
          ),
          _InfoRow(
            icon: Icons.mail_outline,
            text: 'recepcion@clubn.co',
          ),
          _InfoRow(
            icon: Icons.call_outlined,
            text: '(601) 555 0100 · recepción al miembro 24/7',
          ),
          SizedBox(height: 10),
          Text(
            'Club N · el norte nos une desde 1987.',
            style: TextStyle(
              fontStyle: FontStyle.italic,
              fontSize: 12,
              color: Colors.grey,
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Icon(icon, size: 16, color: kDeepGreen.withValues(alpha: 0.7)),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }
}
