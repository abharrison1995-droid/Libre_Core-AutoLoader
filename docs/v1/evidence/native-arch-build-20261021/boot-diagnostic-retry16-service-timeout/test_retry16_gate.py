import unittest

from retry16_gate import (FAILURE_LINE, PtyAnnouncementScanner, READ_ONLY_COMMANDS, SerialGate, query_sequence_ready)


class Retry16GateTests(unittest.TestCase):
    def test_command_allowlist_is_exact(self):
        self.assertEqual(len(READ_ONLY_COMMANDS), 3)
        self.assertEqual(READ_ONLY_COMMANDS[0], "systemctl show systemd-nsresourced.service -p ActiveState -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts -p TimeoutStartUSec -p After -p Requires -p Wants --no-pager")
        self.assertEqual(READ_ONLY_COMMANDS[1], "journalctl -b -u systemd-nsresourced.service -o short-monotonic --no-pager")
        self.assertEqual(READ_ONLY_COMMANDS[2], "systemctl status systemd-nsresourced.service systemd-tmpfiles-setup-dev-early.service --no-pager")

    def test_requires_failure_then_fresh_stable_debug_prompt(self):
        gate = SerialGate(stable_seconds=1.5)
        gate.feed(b"starting systemd\r\n", 1.0)
        self.assertFalse(gate.startup_ready(5.0))
        gate.feed((FAILURE_LINE + "\r\n").encode(), 6.0)
        self.assertFalse(gate.startup_ready(10.0))
        gate.feed(b"sh-5.3# ", 11.0)
        self.assertFalse(gate.startup_ready(12.0))
        self.assertTrue(gate.startup_ready(12.6))

    def test_prompt_before_failure_cannot_be_reused(self):
        gate = SerialGate(stable_seconds=1.0)
        gate.feed(b"sh-5.3# ", 1.0)
        self.assertTrue(gate.stable_prompt(2.1))
        gate.feed((FAILURE_LINE + "\r\n").encode(), 3.0)
        self.assertFalse(gate.startup_ready(5.0))
        gate.feed(b"sh-5.3# ", 6.0)
        self.assertFalse(gate.startup_ready(6.5))
        self.assertTrue(gate.startup_ready(7.1))

    def test_ansi_wrapped_failure_and_prompt_are_recognized(self):
        gate = SerialGate(stable_seconds=0.5)
        gate.feed(b"\x1b[31m" + FAILURE_LINE.encode() + b"\x1b[0m\r\n", 1.0)
        gate.feed(b"\x1b[0msh-5.3# \x1b[0m", 2.0)
        self.assertTrue(gate.startup_ready(2.6))

    def test_unexpected_root_prompt_is_fail_closed(self):
        gate = SerialGate(stable_seconds=0.1)
        gate.feed(b"[FAILED] Failed to start Namespace Resource Manager.\r\nroot@archiso ~# ", 1.0)
        self.assertEqual(gate.unexpected_prompt, "root@archiso ~#")
        self.assertFalse(gate.startup_ready(2.0))

    def test_emergency_or_fatal_state_blocks_queries(self):
        gate = SerialGate(stable_seconds=0.1)
        gate.feed(b"[FAILED] Failed to start Namespace Resource Manager.\r\nsh-5.3# ", 1.0)
        gate.feed(b"Kernel panic - not syncing: test\r\n", 2.0)
        self.assertEqual(gate.fatal_marker, "Kernel panic - not syncing")
        self.assertFalse(gate.startup_ready(4.0))

    def test_each_command_return_requires_new_prompt_bytes(self):
        gate = SerialGate(stable_seconds=0.5)
        gate.feed(b"[FAILED] Failed to start Namespace Resource Manager.\r\nsh-5.3# ", 1.0)
        self.assertTrue(gate.stable_prompt(1.6))
        start = gate.offset
        gate.feed(b"systemctl show output\r\n", 2.0)
        self.assertFalse(gate.stable_prompt(3.0, after_offset=start))
        gate.feed(b"sh-5.3# ", 4.0)
        self.assertFalse(gate.stable_prompt(4.2, after_offset=start))
        self.assertTrue(gate.stable_prompt(4.6, after_offset=start))


    def test_pty_announcement_on_merged_stdout_is_discovered(self):
        scanner = PtyAnnouncementScanner()
        self.assertEqual(
            scanner.feed(b"QEMU started\r\nchar device redirected to /dev/pts/42 (label serial0)\n"),
            ("/dev/pts/42",),
        )
        self.assertEqual(scanner.feed(b""), ())

    def test_split_pty_announcement_is_discovered_once(self):
        scanner = PtyAnnouncementScanner()
        self.assertEqual(scanner.feed(b"char device redirected to /dev/pt"), ())
        self.assertEqual(scanner.feed(b"s/17 (label serial0)\r\n"), ("/dev/pts/17",))
        self.assertEqual(scanner.feed(b"char device redirected to /dev/pts/17 (label serial0)"), ())

    def test_pty_timeout_cannot_send_guest_input(self):
        sent = []
        serial_connected = False
        startup_gates_passed = True
        qemu_running = True
        if query_sequence_ready(serial_connected, startup_gates_passed, qemu_running):
            sent.append("forbidden")
        self.assertEqual(sent, [])
        self.assertFalse(query_sequence_ready(False, True, True))
        self.assertFalse(query_sequence_ready(True, True, False))
        self.assertFalse(query_sequence_ready(True, False, True))


if __name__ == "__main__":
    unittest.main()