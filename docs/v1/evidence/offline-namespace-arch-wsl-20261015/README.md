# Arch WSL offline-namespace probe

This records the production `_establish_offline_network_isolation(subprocess.run)` check on 2026-10-15. Arch WSL reported kernel `6.18.54.1-microsoft-standard-WSL2`, `archiso 91-1`, `util-linux 2.42.4-1`, and `iproute2 7.2.0-1`. Pacman verified the canonical `/usr/bin/unshare` and `/usr/bin/ip` owners. The network-isolation probe created a child with `unshare --user --map-root-user --net`, and the strict production report passed.

The subsequent private mount/PID probe used `unshare --user --map-root-user --net --mount --pid --fork --mount-proc`. It confirmed distinct namespaces and loopback-only networking. Proc, sysfs and tmpfs mounts succeeded; devtmpfs, bind mounts for `/dev` and `/run`, and `devpts gid=5` failed in WSL. No builder command was run. The captured output is in [mount-probe-20261015.txt](mount-probe-20261015.txt).

The integrated production preflight was invoked directly with
`_establish_offline_network_isolation(subprocess.run)`. The first recorded
result, `ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE`, came from a broad OSError
classifier and is retained as historical evidence only. The stricter errno
policy then correctly classified EINVAL as an integrity failure, exposing a
probe argument-plan defect: generic VFS flags had been placed in tmpfs data.
A disposable same-namespace comparison showed the old syscall returned
EINVAL, while the corrected split between mount flags and filesystem data and
the exact util-linux `mount(8)` form succeeded. After that correction, the
direct production preflight reports only `devtmpfs` as unavailable in WSL;
the route probe passes and host mountinfo remains unchanged. No builder/output
was created. See the [errno policy report](mount-errno-classification-20261015.txt),
the [tmpfs argument diagnosis](tmpfs-option-split-diagnostic-20261015.txt),
and the retained [historical production run](production-mount-preflight-20261015.txt).

`SHA256SUMS` covers the evidence files. These results establish the network-isolation probe and successful tmpfs argument handling only in Arch WSL. They do not establish native Linux support, the complete mount set required by Archiso's chroot, offline build success, or ISO boot success.

The post-tmpfs-correction online-candidate readiness check reproduced the
required mount-preflight denial at `devtmpfs` in Arch WSL. It called only the
production preflight, not `execute_build`; therefore it did not sync
repositories, install packages, invoke `mkarchiso`, or create build output.
The exact command and exception are in
[`online-candidate-readiness-preflight-20261015.txt`](online-candidate-readiness-preflight-20261015.txt).
The online build remains blocked in this WSL environment; native Linux
support is not established.
