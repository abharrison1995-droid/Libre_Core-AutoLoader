#!/usr/bin/env python3
import json
import socket
import sys

if len(sys.argv) != 3 or sys.argv[2] not in {"query-status", "quit"}:
    raise SystemExit("usage: qmp_control.py SOCKET query-status|quit")
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(5)
sock.connect(sys.argv[1])
stream = sock.makefile("rwb", buffering=0)
greeting = json.loads(stream.readline())
if "QMP" not in greeting:
    raise SystemExit("missing QMP greeting")
stream.write(b'{"execute":"qmp_capabilities"}\r\n')
capabilities = json.loads(stream.readline())
if "return" not in capabilities:
    raise SystemExit("QMP capabilities rejected")
request = {"execute": sys.argv[2]}
stream.write((json.dumps(request) + "\r\n").encode())
reply = json.loads(stream.readline())
print(json.dumps({"greeting": greeting, "capabilities": capabilities, "reply": reply}, sort_keys=True))
sock.close()
