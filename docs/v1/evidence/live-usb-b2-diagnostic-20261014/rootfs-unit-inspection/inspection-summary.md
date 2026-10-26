# Offline rootfs unit inspection

This inspection reads the unchanged b2 ISO only. ISO SHA-256 is
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`.
The extracted `airootfs.sfs` SHA-512 matches the ISO's `arch/x86_64/airootfs.sha512`
entry. The image package database identifies systemd as `262-1`.

The raw unit files, package record, preset files, and path inventory in this
directory were extracted from that verified SquashFS image. The b2 `90-systemd.preset`
contains `enable systemd-nsresourced.socket`; run8 serial records systemd
creating `/etc/systemd/system/sockets.target.wants/systemd-nsresourced.socket`
at startup. The socket listens at
`/run/systemd/io.systemd.NamespaceResource` and creates aliases at
`/run/systemd/userdb/io.systemd.NamespaceResource` and
`/run/varlink/registry/io.systemd.NamespaceResource`.

Run8 serial identifies the socket request: a Varlink connection invoked
`io.systemd.UserDatabase.GetMemberships` with `groupName=audio` and
`service=io.systemd.DynamicUser`; the recorded reply was
`io.systemd.UserDatabase.NoRecordFound`. The requesting process was not
identified in retained evidence. The systemd 262 manual page packaged in the
image describes `io.systemd.NamespaceResource` as the API for registering user
namespaces and assigning mounts, cgroups, and network interfaces.

The matching service unit has `Requires=systemd-nsresourced.socket`,
`Before=sysinit.target`, `Wants=modprobe@tun.service modprobe@overlay.service`,
and matching `After=` ordering for those two module units. It is
`Type=notify` with `WatchdogSec=3min`. Run8 logs show it waiting for
`modprobe@tun.service`, then starting after the request, timing out during
startup, and receiving `READY=1` only after systemd had begun terminating it.
This is a confirmed socket-triggered slow/failing service job, but the retained
evidence does not establish why the service or its module dependency was slow.

The separate target-order evidence is also relevant. In run8 serial,
`basic.target` is held for `systemd-pcrphase-sysinit.service`; that unit is
ordered `After=sysinit.target` and `Before=basic.target`. `sysinit.target` is
held for `systemd-sysext.service`, which is ordered after `local-fs.target` and
before `sysinit.target`. The log also shows `local-fs.target` waiting for
`var-lib-machines.mount`. In the image, that mount has
`ConditionPathExists=/var/lib/machines.raw`; the extracted SquashFS inventory
contains `/var/lib/machines/` but no `machines.raw` file. The live run still
reported the mount job waiting at its captured snapshot, so its eventual
condition result is not established here.

These are dependency and request-path findings, not a confirmed root cause.
The evidence does not show which pending job first prevented normal startup,
or whether the nsresourced timeout caused any of the target waits. No product
image, profile, unit, or account was changed. The normal BIOS/UEFI app/service
gate remains failed or incomplete as recorded in the parent README.
