import re

ANSI_ESCAPE = re.compile(
    rb"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])"
)
FAILURE_LINE = "[FAILED] Failed to start Namespace Resource Manager."
EXPECTED_ROOT_PROMPT = "sh-5.3#"
UNEXPECTED_PROMPT_PATTERNS = (
    re.compile(r"^#$"),
    re.compile(r"^\$$"),
    re.compile(r"^root@[^\r\n]*[#$]$"),
    re.compile(r"^(?:sh|bash)-[^\r\n]*[#$]$"),
)
NORMAL_TARGET_PATTERNS = (
    "Reached target Multi-User System",
    "Reached target Graphical Interface",
)
FATAL_PATTERNS = (
    "Reached target Emergency Mode",
    "You are in emergency mode",
    "Kernel panic - not syncing",
    "Reached target Power-Off",
    "Reached target Reboot",
)


def normalize_serial(raw: bytes) -> str:
    clean = ANSI_ESCAPE.sub(b"", raw)
    return clean.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def final_prompt_kind(normalized: str):
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return None
    final = lines[-1]
    if final == EXPECTED_ROOT_PROMPT:
        return "expected-root"
    if any(pattern.fullmatch(final) for pattern in UNEXPECTED_PROMPT_PATTERNS):
        return "unexpected"
    return None


class SerialGate:
    def __init__(self, stable_seconds: float = 1.5, tail_limit: int = 65536):
        self.stable_seconds = stable_seconds
        self.tail_limit = tail_limit
        self.raw_tail = b""
        self.offset = 0
        self.last_data_at = 0.0
        self.prompt_since = None
        self.prompt_offset = 0
        self.failure_seen = False
        self.unexpected_prompt = None
        self.fatal_marker = None

    def feed(self, data: bytes, now: float) -> None:
        if not data:
            return
        self.offset += len(data)
        self.last_data_at = now
        self.raw_tail = (self.raw_tail + data)[-self.tail_limit:]
        normalized = normalize_serial(self.raw_tail)
        if FAILURE_LINE in normalized:
            self.failure_seen = True
        for marker in FATAL_PATTERNS:
            if marker in normalized:
                self.fatal_marker = marker
                break
        kind = final_prompt_kind(normalized)
        if kind == "expected-root":
            if self.prompt_since is None:
                self.prompt_since = now
                self.prompt_offset = self.offset
            self.unexpected_prompt = None
        else:
            self.prompt_since = None
            self.prompt_offset = 0
            if kind == "unexpected":
                self.unexpected_prompt = normalized.split("\n")[-1].strip()

    def stable_prompt(self, now: float, after_offset: int = 0) -> bool:
        return (
            self.prompt_since is not None
            and self.prompt_offset > after_offset
            and now - self.prompt_since >= self.stable_seconds
            and now - self.last_data_at >= self.stable_seconds
            and self.unexpected_prompt is None
        )

    def startup_ready(self, now: float) -> bool:
        return self.failure_seen and self.fatal_marker is None and self.stable_prompt(now)


READ_ONLY_COMMANDS = (
    "systemctl show systemd-nsresourced.service -p ActiveState -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts -p TimeoutStartUSec -p After -p Requires -p Wants --no-pager",
    "journalctl -b -u systemd-nsresourced.service -o short-monotonic --no-pager",
    "systemctl status systemd-nsresourced.service systemd-tmpfiles-setup-dev-early.service --no-pager",
)