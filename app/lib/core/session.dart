import 'package:flutter/foundation.dart';
import 'package:club_n_app/core/role.dart';

class ClubUser {
  final String name;
  final String email;
  final Role role;

  const ClubUser({
    required this.name,
    required this.role,
    this.email = '',
  });
}

class SessionController extends ChangeNotifier {
  ClubUser? _user;

  ClubUser? get user => _user;
  bool get isSignedIn => _user != null;

  void signIn(ClubUser u) {
    _user = u;
    notifyListeners();
  }

  void signOut() {
    _user = null;
    notifyListeners();
  }
}
