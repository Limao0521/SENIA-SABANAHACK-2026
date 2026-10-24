import 'package:flutter/foundation.dart';
import 'package:club_n_app/core/models.dart';
import 'package:club_n_app/core/space.dart';

class GuestRepository extends ChangeNotifier {
  final List<Guest> _guests = [];

  List<Guest> get guests => List.unmodifiable(_guests);

  void add(Guest g) {
    _guests.add(g);
    notifyListeners();
  }
}

class ReservationRepository extends ChangeNotifier {
  final List<Reservation> _reservations = [];

  List<Reservation> get reservations => List.unmodifiable(_reservations);

  void add(Reservation r) {
    _reservations.add(r);
    notifyListeners();
  }

  // Solape estricto: start < r.end && r.start < end, mismo espacio.
  bool hasOverlap(ClubSpace space, DateTime start, DateTime end) {
    return _reservations.any(
      (r) =>
          r.space == space &&
          start.isBefore(r.end) &&
          r.start.isBefore(end),
    );
  }

  List<Reservation> byHolder(String name) {
    return _reservations.where((r) => r.holderName == name).toList();
  }
}
