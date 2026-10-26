# Ubuntu WSL production preflight evidence

This directory records the 2026-10-15 production network-isolation preflight
from the shared Windows checkout under Ubuntu 26.04 WSL2. The run failed closed
before namespace or mount checks because the Ubuntu distro has no `pacman` for
the builder's trusted package-ownership check. It is not evidence of an
Archiso build environment or of mount/network capability.

The command and exact result summary are in
[`production-network-preflight-20261015.txt`](production-network-preflight-20261015.txt).
`SHA256SUMS` covers both files.
