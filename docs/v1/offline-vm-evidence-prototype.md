# Offline VM evidence-volume and host-adapter prototype

**Status: prototype only; the offline worker and builder remain unavailable.**
This format is not connected to `BuildResult`, candidate publication, or
`execute_build`, and it does not establish that a real target root fits the
volume policy.

The prototype keeps build evidence on a separate evidence volume. It does not
reserve or infer free space in the ISO output volume, whose existing cap is
16 GiB. The version-3 record binds the operation ID, request digest, source
image digest, package image digest, profile digest, snapshot-lock digest,
snapshot date, candidate ISO SHA-256 and size, and fixed-command argv digest.
It transports hash-bound bytes labelled as a target-root tree, wheel, package
inventory, command status, provenance, and optional logs. The command-status
record must match the operation, request, candidate ISO, and argv digest in the
binding. The host adapter also checks the package inventory against the
existing host package-version validation and checks the provenance record's
internal links to the request, ISO, command, wheel, package inventory, and
target-root inventory. These checks establish consistency only: the records
are supplied by the guest and are not authenticated or signed, so they do not
prove who produced them or independently establish build provenance.

The separate output-result protocol is version 3. Its candidate header,
service receipt, commit marker, and terminal marker all carry the exact
evidence-volume size and SHA-256. The intended write order is: copy and fsync
the ISO bytes to the output volume; finish, fsync, and hash the separate
evidence volume; then publish the service receipt and output header, commit,
and terminal marker, with the terminal marker last. The host independently
rehashes the evidence volume and rejects it unless its size and digest match
the final output protocol. This closes substitution of a different
same-request/same-ISO evidence bundle after the output commit was written.

The worker layout now binds a separate, fixed `LC-EVIDENCE-RW` device and the
guest module contains a prototype block-device probe/writer. It checks the
request-bound capacity and device identity, requires the device to be
unmounted, streams the bundle, fsyncs and reads it back, and leaves final output
commit/terminal publication to the existing protocol. A helper can derive the
candidate ISO, target-root tree, and built wheel from fixed worker paths. The
normal guest entry point still returns `BUILD_BACKEND_UNAVAILABLE`; it does not
invoke the evidence writer or supply all mandatory command-status,
package-inventory, and provenance records. The writer is disconnected from
`main`, production deployment is unset, and publication remains unavailable.
No real target root, wheel, package inventory, or build provenance was
produced, and no guest/QEMU build succeeded. Even with cross-volume hash
binding, evidence is not authenticated against a malicious worker; this remains
a pending-only prototype.

The parser streams payload hashing and bounds the manifest to 1 MiB, entries to
100,000, and the fixed evidence-device capacity to 2 MiB through a provisional
64 GiB maximum. Physical capacity is distinct from logical bundle length. The
64 GiB bound is a defensive prototype ceiling, **not a measured capacity or an
acceptance limit**. No completed native build has been measured against it.
The version-3 cross-volume binding adds a second streaming pass over the full
evidence volume; its runtime and storage cost have not been measured on a real
worker output.
Version 3 rejects special files and symlinks outside `target-root`. Within
`target-root`, a symlink is carried as hashed payload bytes for its target. The
target must be valid UTF-8 POSIX text with no NUL or backslash, and its lexical
resolution must stay inside the target root. Absolute in-root targets are
interpreted from that root; literal `/dev/null` is the sole external absolute
target exception, and only at exact
`target-root/etc/systemd/system/<unit>` paths whose unit names come from
`usb_builder.build_usb.NETWORK_DISABLED_SYSTEMD_UNITS`. The validator imports
that reviewed policy instead of maintaining a duplicate unit-name list.
Symlink parents must be explicit directories. Extraction writes and verifies
regular files first, then creates
symlinks through no-follow directory descriptors, so links cannot redirect
file writes. This transport behavior has not been checked against actual
mkarchiso output; compatibility remains unmeasured until a real, independently
validated build is available.

Extraction is available only into a fresh private directory under an existing
current-user-owned mode-0700 operation root named for the bound operation ID.
It uses no-follow directory/file opens, exclusive file creation, bounded
streaming writes, per-file hashes, and a completion marker published after the
tree is synced. A caller may treat extraction as successful only after the
extractor returns successfully and a fresh verification confirms the extracted
tree and marker identity; the marker by itself is never proof of success. If
marker removal or its directory sync fails while reporting an extraction
failure, the error is surfaced and the operation must be treated as incomplete
and quarantined. Partial bytes may remain for inspection. Existing
destinations and replay are rejected.

The pending-only host adapter extracts the bundle into the exact operation's
fresh private evidence directory, rechecks the extraction after the existing
USB profile, target-root, and package validators run, and returns a report with
`published=False`. Its package/provenance record schemas are prototype
consistency checks; neither the adapter nor its synthetic tests authenticate a
guest or publish a `BuildResult`. The adapter rejects swapped request,
operation, command, package, provenance, and ISO bindings; incomplete or
replayed evidence; linked evidence volumes; and concurrent evidence-volume or
extracted-tree changes.

Focused tests exercise binding, tampering, truncation, path traversal,
duplicate/special entries, incomplete status, replay, streaming, and the
pending-only adapter. These are synthetic transport, consistency, and
failure-injection checks only, including concurrent mutation. They are not a
real guest build or evidence-authentication proof. Production deployment pin
remains unset; the guest executor, native no-network VM build, independently
authenticated package/source provenance, target-root validation against real
mkarchiso output, and ISO boot gates remain open. No firmware or hardware
operation is part of this prototype.
