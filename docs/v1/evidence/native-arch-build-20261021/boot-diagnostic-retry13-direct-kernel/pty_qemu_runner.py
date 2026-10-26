#!/usr/bin/env python3
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import tty

if len(sys.argv) < 5:
    raise SystemExit("usage: pty_qemu_runner.py SERIAL_LOG INPUT_LOG WALL_SECONDS COMMAND [ARGS...]")
serial_log_path, input_log_path, wall_text = sys.argv[1:4]
command = sys.argv[4:]
wall_seconds = int(wall_text)
if wall_seconds < 1 or wall_seconds > 600:
    raise SystemExit("wall bound must be 1..600 seconds")
if not os.isatty(sys.stdin.fileno()) or not os.isatty(sys.stdout.fileno()):
    raise SystemExit("interactive outer PTY required")

start = time.monotonic()
pid, master_fd = pty.fork()
if pid == 0:
    os.execvpe(command[0], command, os.environ.copy())

outer_in = sys.stdin.fileno()
outer_out = sys.stdout.fileno()
old_outer = termios.tcgetattr(outer_in)
rows, cols = 24, 80
try:
    winsize = fcntl.ioctl(outer_in, termios.TIOCGWINSZ, b"\0" * 8)
    rows, cols, _, _ = struct.unpack("HHHH", winsize)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
except OSError:
    pass

tty.setraw(outer_in)
os.set_blocking(master_fd, False)
result = None
wall_expired = False
term_sent_at = None
try:
    with open(serial_log_path, "ab", buffering=0) as serial_log, open(input_log_path, "ab", buffering=0) as input_log:
        while True:
            now = time.monotonic()
            if now - start >= wall_seconds and term_sent_at is None:
                wall_expired = True
                term_sent_at = now
                print("\r\nPTY_RUNNER_WALL_CAP_REACHED\r\n", flush=True)
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if term_sent_at is not None and now - term_sent_at >= 5:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            readable, _, _ = select.select([master_fd, outer_in], [], [], 0.2)
            if master_fd in readable:
                try:
                    data = os.read(master_fd, 4096)
                except (BlockingIOError, OSError):
                    data = b""
                if data:
                    serial_log.write(data)
                    os.write(outer_out, data)
            if outer_in in readable:
                try:
                    data = os.read(outer_in, 4096)
                except OSError:
                    data = b""
                if not data:
                    break
                input_log.write(data)
                try:
                    os.write(master_fd, data)
                except OSError:
                    pass
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                if os.WIFEXITED(status):
                    result = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    result = 128 + os.WTERMSIG(status)
                else:
                    result = 1
                break
finally:
    termios.tcsetattr(outer_in, termios.TCSADRAIN, old_outer)
    try:
        os.close(master_fd)
    except OSError:
        pass
    if result is None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            _, status = os.waitpid(pid, 0)
            result = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            result = 1
elapsed = time.monotonic() - start
print("\r\nPTY_RUNNER_EXIT=%s ELAPSED_SECONDS=%.3f WALL_CAP=%s\r\n" % (result, elapsed, wall_expired), flush=True)
raise SystemExit(124 if wall_expired else int(result))
