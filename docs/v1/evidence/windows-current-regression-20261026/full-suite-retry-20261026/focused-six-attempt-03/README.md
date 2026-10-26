# Focused Windows offline-worker portability regression — attempt 03

The six tests mapped from the interrupted full-run progress stream were rerun
with the two newly separated POSIX-only cases included. Result: **5 passed,
2 skipped, 0 failed** in 3.52 seconds (pytest exit `0`). The skips are
intentional platform boundaries: durable seed inventory publication depends
on POSIX descriptor/directory `fsync`, and launching the sealed worker is
supported only by the reviewed Linux host contract. The cross-platform
seed-policy rejection assertion still ran and passed.

This run used Python 3.12.14 and pytest 9.1.1 on Windows 10.0.19044. The
current 204-path source/test manifest is captured before execution and matches
the post-run recheck. The exact command, environment, JUnit report, and full
pytest output are retained here; `SHA256SUMS` covers this directory's files
except itself.

The test also confirms that the guest mount protocol retains POSIX path
separators when imported by the Windows desktop process. This is a software
portability check only. It does not validate the worker VM, ROM build, USB
image, SPI hardware, recovery, or boot.
