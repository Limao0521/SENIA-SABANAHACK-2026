enum Role { vip, regular, staff }

extension RoleLabel on Role {
  String get label => switch (this) {
    Role.vip => 'Membresía VIP',
    Role.regular => 'Membresía Regular',
    Role.staff => 'Staff',
  };

  String get shortTag => switch (this) {
    Role.vip => 'VIP',
    Role.regular => 'REG',
    Role.staff => 'STF',
  };

  String get qrAssetPath => switch (this) {
    Role.vip => 'assets/qr/qr_vip.png',
    Role.regular => 'assets/qr/qr_regular.png',
    Role.staff => 'assets/qr/qr_staff.png',
  };
}
