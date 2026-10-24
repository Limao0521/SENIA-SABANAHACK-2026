import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:club_n_app/core/role.dart';
import 'package:club_n_app/core/session.dart';
import 'carnet_screen.dart';
import 'guests_screen.dart';
import 'reservations_screen.dart';
import 'club_info_screen.dart';
import 'login_screen.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _currentIndex = 0;

  @override
  Widget build(BuildContext context) {
    final session = context.watch<SessionController>();
    final user = session.user!;
    final isStaff = user.role == Role.staff;

    if (isStaff) {
      return Scaffold(
        appBar: AppBar(
          title: const Text('Club N · Staff'),
          backgroundColor: kDeepGreen,
          foregroundColor: Colors.white,
          actions: [
            IconButton(
              icon: const Icon(Icons.logout),
              tooltip: 'Salir',
              onPressed: () => session.signOut(),
            ),
          ],
        ),
        body: const CarnetPage(),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              user.name,
              style: const TextStyle(fontSize: 16),
            ),
            Text(
              user.role.label,
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w400,
                color: user.role == Role.vip
                    ? kGold
                    : Colors.white70,
              ),
            ),
          ],
        ),
        backgroundColor: kDeepGreen,
        foregroundColor: Colors.white,
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: 'Salir',
            onPressed: () => session.signOut(),
          ),
        ],
      ),
      body: IndexedStack(
        index: _currentIndex,
        children: const [
          ClubInfoPage(),
          CarnetPage(),
          GuestsPage(),
          ReservationsPage(),
        ],
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _currentIndex,
        onTap: (i) => setState(() => _currentIndex = i),
        type: BottomNavigationBarType.fixed,
        selectedItemColor: kDeepGreen,
        unselectedItemColor: Colors.grey.shade500,
        items: const [
          BottomNavigationBarItem(
            icon: Icon(Icons.home_outlined),
            activeIcon: Icon(Icons.home),
            label: 'Inicio',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.badge_outlined),
            activeIcon: Icon(Icons.badge),
            label: 'Carnet',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.person_add_outlined),
            activeIcon: Icon(Icons.person_add),
            label: 'Invitados',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.event_outlined),
            activeIcon: Icon(Icons.event),
            label: 'Reservas',
          ),
        ],
      ),
    );
  }
}
