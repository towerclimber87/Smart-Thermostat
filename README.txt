SMART THERMOSTAT - REPEATABLE SD ENDURANCE DEPLOYMENT
====================================================
Target hardware/software: identical Raspberry Pi thermostat units using
/home/david/Smart-Thermostat-Development.

This package repeats the changes already validated on the LivingroomClimate
unit without repeating the diagnostic discovery steps.

WHAT IT APPLIES
---------------
1. Installs only the six application files changed for SD endurance.
2. Runs the thermostat's normal native installer so services/config stay in
   the supported state.
3. Keeps systemd journal volatile/RAM-backed.
4. Configures rpi-swap Mechanism=zram so swap stays entirely in compressed RAM.
5. Does NOT manually swapoff, delete /var/swap, or rewrite /etc/fstab.
   rpi-swap performs the safe swap transition during reboot.
6. Verifies noatime but does not edit fstab if the clone unexpectedly differs.
7. Creates a one-time backup under /var/backups/smart-thermostat-sd-endurance/.

INSTALL ON EACH IDENTICAL UNIT
------------------------------
Extract this ZIP on the Pi, cd into this folder, then run:

  sudo ./apply-sd-endurance.sh

The script validates the configuration and automatically reboots only after
all required pre-reboot checks pass.

AFTER THE PI COMES BACK
-----------------------
Run:

  ./verify-sd-endurance.sh

The expected final line is:

  ALL SD-ENDURANCE CHECKS PASSED

OPTIONAL
--------
To apply everything without automatically rebooting:

  sudo ./apply-sd-endurance.sh --no-reboot

SAFETY / ROLLBACK
-----------------
The application files touched by the package are backed up before replacement.
The script uses compare-before-write behavior and can be run more than once.
It does not disable swap; it changes swap from zram+file to pure zram.
It intentionally does not make broad read-only-root, tmpfs-/tmp, or fstab
changes because those carry more appliance/update risk than the measured SD
write reduction justifies.
