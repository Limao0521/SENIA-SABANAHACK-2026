import 'package:club_n_app/core/role.dart';
import 'package:club_n_app/core/session.dart';

class ClubAccount {
  final String username;
  final String password;
  final ClubUser user;

  const ClubAccount({
    required this.username,
    required this.password,
    required this.user,
  });
}

const List<ClubAccount> kDemoAccounts = [
  ClubAccount(
    username: 'vip@clubn.co',
    password: 'vip123',
    user: ClubUser(
      name: 'Laura Franco',
      email: 'vip@clubn.co',
      role: Role.vip,
    ),
  ),
  ClubAccount(
    username: 'regular@clubn.co',
    password: 'regular123',
    user: ClubUser(
      name: 'Sebastian Ulloa',
      email: 'regular@clubn.co',
      role: Role.regular,
    ),
  ),
  ClubAccount(
    username: 'staff@clubn.co',
    password: 'staff123',
    user: ClubUser(
      name: 'Luis Gonzalez',
      email: 'staff@clubn.co',
      role: Role.staff,
    ),
  ),
];

class AuthService {
  // MVP sin backend: valida contra kDemoAccounts.
  // Aquí se conectaría el backend real (API REST, Firebase Auth, etc.)
  ClubUser? authenticate(String username, String password) {
    final normalizedUsername = username.trim().toLowerCase();
    for (final account in kDemoAccounts) {
      if (account.username == normalizedUsername &&
          account.password == password) {
        return account.user;
      }
    }
    return null;
  }
}
