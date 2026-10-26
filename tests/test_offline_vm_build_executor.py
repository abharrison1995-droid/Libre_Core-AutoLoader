# Reconstructed from the cached pytest-rewritten CPython 3.12 bytecode; original source bytes were unavailable.
from __future__ import annotations
import builtins as __py_builtins
import _pytest.assertion.rewrite as __pytest_ar
import hashlib
import os
import stat
import subprocess
import tarfile
import io
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any
import pytest
from usb_builder import offline_vm_build as build

@pytest.mark.parametrize(('operation', 'args'), ((build._linux_effective_uid, ()), (build._linux_stat_rdev, (os.stat(__file__),)), (build._linux_ioctl, (-1, 0, b'\x00')), (build._linux_pread, (-1, 1, 0)), (build._linux_kill_process_group, (1, 'SIGTERM'))))
def test_linux_primitives_fail_closed_off_linux(operation: 'Any', args: 'tuple[Any, ...]', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    monkeypatch.setattr(build, '_linux_runtime_available', lambda: False)
    with pytest.raises(build.OfflineVmBuildError, match='supported only inside Linux'):
        operation(*args)

def _state(**overrides: 'Any') -> 'build.GuestSecurityState':
    values = {'account': 'fwbuild', 'uid': 1000, 'gid': 1000, 'supplementary_gids': (1000,), 'cap_effective': '0000000000000000', 'cap_permitted': '0000000000000000', 'cap_ambient': '0000000000000000', 'no_new_privs': False, 'network_namespace': 'net:[4026533000]', 'interfaces': ('lo',), 'ipv4_routes': (), 'ipv6_routes': ()}
    values.update(overrides)
    return build.GuestSecurityState(**values)

def _successful_process_result(invocation: 'build.BuildInvocation') -> 'build.BuildProcessResult':
    return build.BuildProcessResult(service_name=build.BUILD_SERVICE_NAME, argv_sha256=build._invocation_sha256(invocation), returncode=0, timed_out=False, elapsed_seconds=0.5, no_new_privs_before=True, no_new_privs_during=False, network_namespace_before='net:[4026533000]', network_namespace_during='net:[4026533000]', pacstrap_exit_codes=(0, 0), account='fwbuild', uid=1000, gid=1000, supplementary_gids=(1000,), cap_effective='0000000000000000', cap_permitted='0000000000000000', cap_ambient='0000000000000000', interfaces=('lo',), ipv4_routes=(), ipv6_routes=(), cap_bounding=build.CAPABILITY_BOUNDING_MASK)

def _private_file(path: 'Path', data: 'bytes', mode: 'int') -> 'None':
    path.write_bytes(data)
    if os.name == 'posix':
        path.chmod(mode)

@pytest.fixture
def executor_case(tmp_path: 'Path', monkeypatch: 'pytest.MonkeyPatch') -> 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]':
    scratch = tmp_path / 'scratch'
    scratch.mkdir(mode=448)
    if os.name == 'posix':
        scratch.chmod(448)
    work = scratch / 'archiso-work'
    output = scratch / 'image-output'
    temp = scratch / 'tmp'
    overlay_dir = scratch / 'bin'
    for directory in (work, output, temp, overlay_dir):
        directory.mkdir(mode=448)
        if os.name == 'posix':
            directory.chmod(448)
    monkeypatch.setattr(build, 'WORKER_ROOT', str(scratch))
    monkeypatch.setattr(build, 'WORKER_WORK', str(work))
    monkeypatch.setattr(build, 'WORKER_OUTPUT', str(output))
    monkeypatch.setattr(build, 'WORKER_PACSTRAP_BIN', str(overlay_dir))
    monkeypatch.setattr(build, 'PACSTRAP_EXIT_CODES_PATH', str(scratch / 'pacstrap-exit-codes'))
    dummy_pacstrap = b'pinned-test-pacstrap\n'
    monkeypatch.setattr(build, 'PACSTRAP_BINARY_SHA256', hashlib.sha256(dummy_pacstrap).hexdigest())
    wrapper = overlay_dir / 'pacstrap'
    real = overlay_dir / 'pacstrap.real'
    config = scratch / 'pacman.conf'
    _private_file(wrapper, build.PACSTRAP_WRAPPER_BYTES, 448)
    _private_file(real, dummy_pacstrap, 448)
    _private_file(config, build.PACMAN_CONFIG_BYTES, 384)
    _private_file(Path(build.PACSTRAP_EXIT_CODES_PATH), b'', 384)
    state = _state(uid=os.geteuid() if hasattr(os, 'geteuid') else 1000, gid=os.getegid() if hasattr(os, 'getegid') else 1000)
    state = replace(state, supplementary_gids=(state.gid,))
    invocation = build._expected_fixed_invocation('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', wrapper, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
    return (scratch, invocation, state)

class _FakeProcess:

    def __init__(self, returncode: 'int', *, timeout: 'bool'=False) -> 'None':
        self.returncode = None
        self.pid = 481516
        self._target_returncode = returncode
        self._timeout = timeout

    def wait(self, *, timeout: 'float') -> 'int':
        __py_assert3 = build.BUILD_TIMEOUT_SECONDS
        __py_assert1 = timeout == __py_assert3
        if not __py_assert1:
            __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py4)s\n{%(py4)s = %(py2)s.BUILD_TIMEOUT_SECONDS\n}',), (timeout, __py_assert3)) % {'py0': __pytest_ar._saferepr(timeout) if 'timeout' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(timeout) else 'timeout', 'py2': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py4': __pytest_ar._saferepr(__py_assert3)}
            __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
            raise AssertionError(__pytest_ar._format_explanation(__py_format7))
        __py_assert1 = __py_assert3 = None
        if self._timeout:
            raise subprocess.TimeoutExpired('mkarchiso', timeout)
        self.returncode = self._target_returncode
        return self.returncode

    def poll(self) -> 'int | None':
        return self.returncode

def _fake_popen_factory(scratch: 'Path', *, builder_rc: 'int'=0, pacstrap_codes: 'bytes'=b'0\n', timeout: 'bool'=False, calls: 'list[tuple[tuple[str, ...], dict[str, Any]]] | None'=None) -> 'Any':

    def popen(argv: 'tuple[str, ...]', **kwargs: 'Any') -> '_FakeProcess':
        if calls is not None:
            calls.append((argv, kwargs))
        output = Path(build.WORKER_OUTPUT)
        (output / 'librecore-v1.iso').write_bytes(b'I' * 36864)
        Path(build.PACSTRAP_EXIT_CODES_PATH).write_bytes(pacstrap_codes)
        return _FakeProcess(builder_rc, timeout=timeout)
    return popen

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_fixed_executor_uses_only_canonical_mkarchiso_process_policy(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]') -> 'None':
    scratch, invocation, state = executor_case
    calls = []
    executor = build.FixedMkarchisoOperations(state, popen=_fake_popen_factory(scratch, calls=calls), clock=iter((10.0, 10.25)).__next__)
    result = executor.run_fixed_builder(invocation)
    __py_assert2 = len(calls)
    __py_assert5 = 1
    __py_assert4 = __py_assert2 == __py_assert5
    if not __py_assert4:
        __py_format7 = __pytest_ar._call_reprcompare(('==',), (__py_assert4,), ('%(py3)s\n{%(py3)s = %(py0)s(%(py1)s)\n} == %(py6)s',), (__py_assert2, __py_assert5)) % {'py0': __pytest_ar._saferepr(len) if 'len' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(len) else 'len', 'py1': __pytest_ar._saferepr(calls) if 'calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(calls) else 'calls', 'py3': __pytest_ar._saferepr(__py_assert2), 'py6': __pytest_ar._saferepr(__py_assert5)}
        __py_format9 = 'assert %(py8)s' % {'py8': __py_format7}
        raise AssertionError(__pytest_ar._format_explanation(__py_format9))
    __py_assert2 = __py_assert4 = __py_assert5 = None
    argv, kwargs = calls[0]
    __py_assert3 = invocation.argv
    __py_assert1 = argv == __py_assert3
    if not __py_assert1:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py4)s\n{%(py4)s = %(py2)s.argv\n}',), (argv, __py_assert3)) % {'py0': __pytest_ar._saferepr(argv) if 'argv' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(argv) else 'argv', 'py2': __pytest_ar._saferepr(invocation) if 'invocation' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(invocation) else 'invocation', 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert1 = __py_assert3 = None
    __py_assert0 = kwargs['executable']
    __py_assert4 = build.MKARCHISO_PATH
    __py_assert2 = __py_assert0 == __py_assert4
    if not __py_assert2:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py5)s\n{%(py5)s = %(py3)s.MKARCHISO_PATH\n}',), (__py_assert0, __py_assert4)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert0 = __py_assert2 = __py_assert4 = None
    __py_assert0 = kwargs['cwd']
    __py_assert3 = '/'
    __py_assert2 = __py_assert0 == __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None
    __py_assert0 = kwargs['env']
    __py_assert5 = invocation.env
    __py_assert7 = dict(__py_assert5)
    __py_assert2 = __py_assert0 == __py_assert7
    if not __py_assert2:
        __py_format9 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py8)s\n{%(py8)s = %(py3)s(%(py6)s\n{%(py6)s = %(py4)s.env\n})\n}',), (__py_assert0, __py_assert7)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(dict) if 'dict' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(dict) else 'dict', 'py4': __pytest_ar._saferepr(invocation) if 'invocation' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(invocation) else 'invocation', 'py6': __pytest_ar._saferepr(__py_assert5), 'py8': __pytest_ar._saferepr(__py_assert7)}
        __py_format11 = 'assert %(py10)s' % {'py10': __py_format9}
        raise AssertionError(__pytest_ar._format_explanation(__py_format11))
    __py_assert0 = __py_assert2 = __py_assert5 = __py_assert7 = None
    __py_assert0 = kwargs['stdin']
    __py_assert4 = subprocess.DEVNULL
    __py_assert2 = __py_assert0 == __py_assert4
    if not __py_assert2:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py5)s\n{%(py5)s = %(py3)s.DEVNULL\n}',), (__py_assert0, __py_assert4)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(subprocess) if 'subprocess' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(subprocess) else 'subprocess', 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert0 = __py_assert2 = __py_assert4 = None
    __py_assert1 = kwargs['stdout']
    __py_assert4 = isinstance(__py_assert1, int)
    if not __py_assert4:
        __py_format6 = 'assert %(py5)s\n{%(py5)s = %(py0)s(%(py2)s, %(py3)s)\n}' % {'py0': __pytest_ar._saferepr(isinstance) if 'isinstance' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(isinstance) else 'isinstance', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(int) if 'int' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(int) else 'int', 'py5': __pytest_ar._saferepr(__py_assert4)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert4 = None
    __py_assert0 = kwargs['stderr']
    __py_assert4 = subprocess.STDOUT
    __py_assert2 = __py_assert0 == __py_assert4
    if not __py_assert2:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py5)s\n{%(py5)s = %(py3)s.STDOUT\n}',), (__py_assert0, __py_assert4)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(subprocess) if 'subprocess' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(subprocess) else 'subprocess', 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert0 = __py_assert2 = __py_assert4 = None
    __py_assert0 = kwargs['close_fds']
    __py_assert3 = True
    __py_assert2 = __py_assert0 is __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('is',), (__py_assert2,), ('%(py1)s is %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None
    __py_assert0 = kwargs['shell']
    __py_assert3 = False
    __py_assert2 = __py_assert0 is __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('is',), (__py_assert2,), ('%(py1)s is %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None
    __py_assert0 = kwargs['start_new_session']
    __py_assert3 = True
    __py_assert2 = __py_assert0 is __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('is',), (__py_assert2,), ('%(py1)s is %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None
    __py_assert0 = 'pass_fds'
    __py_assert2 = __py_assert0 not in kwargs
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('not in',), (__py_assert2,), ('%(py1)s not in %(py3)s',), (__py_assert0, kwargs)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(kwargs) if 'kwargs' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(kwargs) else 'kwargs'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert1 = []
    __py_assert3 = result.returncode
    __py_assert6 = 0
    __py_assert5 = __py_assert3 == __py_assert6
    __py_assert0 = __py_assert5
    if __py_assert5:
        __py_assert12 = result.timed_out
        __py_assert14 = not __py_assert12
        __py_assert0 = __py_assert14
    if not __py_assert0:
        __py_format8 = __pytest_ar._call_reprcompare(('==',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s.returncode\n} == %(py7)s',), (__py_assert3, __py_assert6)) % {'py2': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py4': __pytest_ar._saferepr(__py_assert3), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format10 = '%(py9)s' % {'py9': __py_format8}
        __py_assert1.append(__py_format10)
        if __py_assert5:
            __py_format15 = 'not %(py13)s\n{%(py13)s = %(py11)s.timed_out\n}' % {'py11': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py13': __pytest_ar._saferepr(__py_assert12)}
            __py_assert1.append(__py_format15)
        __py_format16 = __pytest_ar._format_boolop(__py_assert1, 0) % {}
        __py_format18 = 'assert %(py17)s' % {'py17': __py_format16}
        raise AssertionError(__pytest_ar._format_explanation(__py_format18))
    __py_assert0 = __py_assert1 = __py_assert3 = __py_assert5 = __py_assert6 = __py_assert12 = __py_assert14 = None
    __py_assert1 = result.pacstrap_exit_codes
    __py_assert4 = (0,)
    __py_assert3 = __py_assert1 == __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.pacstrap_exit_codes\n} == %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = result.argv_sha256
    __py_assert5 = build._invocation_sha256
    __py_assert8 = __py_assert5(invocation)
    __py_assert3 = __py_assert1 == __py_assert8
    if not __py_assert3:
        __py_format10 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.argv_sha256\n} == %(py9)s\n{%(py9)s = %(py6)s\n{%(py6)s = %(py4)s._invocation_sha256\n}(%(py7)s)\n}',), (__py_assert1, __py_assert8)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py6': __pytest_ar._saferepr(__py_assert5), 'py7': __pytest_ar._saferepr(invocation) if 'invocation' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(invocation) else 'invocation', 'py9': __pytest_ar._saferepr(__py_assert8)}
        __py_format12 = 'assert %(py11)s' % {'py11': __py_format10}
        raise AssertionError(__pytest_ar._format_explanation(__py_format12))
    __py_assert1 = __py_assert3 = __py_assert5 = __py_assert8 = None

@pytest.mark.parametrize('field,value', [('argv', ('/bin/sh', '-c', 'true')), ('cwd', '/tmp')])
def test_fixed_executor_rejects_command_drift_before_subprocess(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', field: 'str', value: 'Any') -> 'None':
    _scratch, invocation, state = executor_case
    calls = []
    executor = build.FixedMkarchisoOperations(state, popen=lambda *_args, **_kwargs: calls.append(object()))
    with pytest.raises(build.OfflineVmBuildError, match='non-canonical invocation'):
        executor.run_fixed_builder(replace(invocation, **{field: value}))
    __py_assert2 = []
    __py_assert1 = calls == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (calls, __py_assert2)) % {'py0': __pytest_ar._saferepr(calls) if 'calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(calls) else 'calls', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None

def test_fixed_executor_rejects_route_or_capability_drift_before_subprocess(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]') -> 'None':
    _scratch, invocation, state = executor_case
    calls = []
    unsafe = replace(state, interfaces=('lo', 'eth0'))
    with pytest.raises(build.OfflineVmBuildError, match='route-free loopback-only'):
        build.FixedMkarchisoOperations(unsafe, popen=lambda *_args, **_kwargs: calls.append(object())).run_fixed_builder(invocation)
    __py_assert2 = []
    __py_assert1 = calls == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (calls, __py_assert2)) % {'py0': __pytest_ar._saferepr(calls) if 'calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(calls) else 'calls', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None

def test_preflight_and_service_require_only_the_exact_map_helper_bounding_mask() -> 'None':
    good = _state()
    build.validate_build_service_security(good)
    with pytest.raises(build.OfflineVmBuildError, match='capability bounding set'):
        build.validate_build_service_security(replace(good, cap_bounding='ffffffffffffffff'))
    isolation = type('Isolation', (), {'interfaces': ('lo',), 'ipv4_routes': (), 'ipv6_routes': ()})()
    with pytest.raises(build.OfflineVmBuildError, match='capability bounding set'):
        build.validate_preflight_security(replace(good, no_new_privs=True, cap_bounding='0'), isolation)

def test_path_trigger_waits_for_result_without_unprivileged_systemctl(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch', tmp_path: 'Path') -> 'None':
    _scratch, invocation, _state_value = executor_case
    result_path = tmp_path / 'service-result.json'
    ready_path = tmp_path / 'service-ready.json'
    started_path = tmp_path / 'service-started.json'
    monkeypatch.setattr(build, 'BUILD_RESULT_PATH', str(result_path))
    monkeypatch.setattr(build, 'BUILD_RESULT_READY_PATH', str(ready_path))
    monkeypatch.setattr(build, 'BUILD_SERVICE_STARTED_PATH', str(started_path))
    monkeypatch.setattr(build, '_verify_installed_service_unit', lambda _path: None)
    monkeypatch.setattr(build, '_verify_installed_path_unit', lambda _path: None)
    from usb_builder import offline_vm_build_service
    monkeypatch.setattr(offline_vm_build_service, 'load_invocation', lambda: (invocation, {}))
    pending = build.GuestBuildResult(status='INCOMPLETE_BUILD_EXECUTION', operation_id=invocation.operation_id, request_sha256=invocation.request_sha256, iso_path=None, iso_size=0, iso_sha256=None, command_argv_sha256=build._invocation_sha256(invocation), elapsed_seconds=1.0, commands_run=(build.BUILD_SERVICE_NAME,), cleanup_complete=True)
    monkeypatch.setattr(build, '_load_service_result', lambda _invocation: pending)
    now = [0.0]

    def fake_sleep(delay: 'float') -> 'None':
        now[0] += delay
        result_path.write_bytes(b'complete-result-marker')
        ready_path.write_bytes(b'final-ready-marker')
    observed = build.activate_fixed_build_service(invocation, unit_path=tmp_path / 'service.unit', path_unit_path=tmp_path / 'service.path', clock=lambda: now[0], sleep=fake_sleep, poll_interval_seconds=0.1)
    __py_assert1 = observed is pending
    if not __py_assert1:
        __py_format3 = __pytest_ar._call_reprcompare(('is',), (__py_assert1,), ('%(py0)s is %(py2)s',), (observed, pending)) % {'py0': __pytest_ar._saferepr(observed) if 'observed' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(observed) else 'observed', 'py2': __pytest_ar._saferepr(pending) if 'pending' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(pending) else 'pending'}
        __py_format5 = 'assert %(py4)s' % {'py4': __py_format3}
        raise AssertionError(__pytest_ar._format_explanation(__py_format5))
    __py_assert1 = None
    __py_assert1 = result_path.read_bytes
    __py_assert3 = __py_assert1()
    __py_assert6 = b'complete-result-marker'
    __py_assert5 = __py_assert3 == __py_assert6
    if not __py_assert5:
        __py_format8 = __pytest_ar._call_reprcompare(('==',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.read_bytes\n}()\n} == %(py7)s',), (__py_assert3, __py_assert6)) % {'py0': __pytest_ar._saferepr(result_path) if 'result_path' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result_path) else 'result_path', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format10 = 'assert %(py9)s' % {'py9': __py_format8}
        raise AssertionError(__pytest_ar._format_explanation(__py_format10))
    __py_assert1 = __py_assert3 = __py_assert5 = __py_assert6 = None
    __py_assert1 = ready_path.read_bytes
    __py_assert3 = __py_assert1()
    __py_assert6 = b'final-ready-marker'
    __py_assert5 = __py_assert3 == __py_assert6
    if not __py_assert5:
        __py_format8 = __pytest_ar._call_reprcompare(('==',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.read_bytes\n}()\n} == %(py7)s',), (__py_assert3, __py_assert6)) % {'py0': __pytest_ar._saferepr(ready_path) if 'ready_path' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(ready_path) else 'ready_path', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format10 = 'assert %(py9)s' % {'py9': __py_format8}
        raise AssertionError(__pytest_ar._format_explanation(__py_format10))
    __py_assert1 = __py_assert3 = __py_assert5 = __py_assert6 = None

def test_path_trigger_waits_for_terminal_after_commit_before_accepting_candidate(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch', tmp_path: 'Path') -> 'None':
    _scratch, invocation, _state_value = executor_case
    result_path = tmp_path / 'service-result.json'
    ready_path = tmp_path / 'service-ready.json'
    started_path = tmp_path / 'service-started.json'
    monkeypatch.setattr(build, 'BUILD_RESULT_PATH', str(result_path))
    monkeypatch.setattr(build, 'BUILD_RESULT_READY_PATH', str(ready_path))
    monkeypatch.setattr(build, 'BUILD_SERVICE_STARTED_PATH', str(started_path))
    monkeypatch.setattr(build, '_verify_installed_service_unit', lambda _path: None)
    monkeypatch.setattr(build, '_verify_installed_path_unit', lambda _path: None)
    from usb_builder import offline_vm_build_service
    monkeypatch.setattr(offline_vm_build_service, 'load_invocation', lambda: (invocation, {}))
    candidate = build.GuestBuildResult(status='CANDIDATE_ISO_PENDING_HOST_VALIDATION', operation_id=invocation.operation_id, request_sha256=invocation.request_sha256, iso_path=f'{build.WORKER_OUTPUT}/candidate.iso', iso_size=36864, iso_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', command_argv_sha256=build._invocation_sha256(invocation), elapsed_seconds=1.0, commands_run=(build.BUILD_SERVICE_NAME,), cleanup_complete=True)
    monkeypatch.setattr(build, '_load_service_result', lambda _invocation: candidate)
    output_state = {'commit': True, 'terminal': False}

    def final_output_marker_present(_request: 'dict[str, object]') -> 'bool':
        return output_state['commit'] and output_state['terminal']
    monkeypatch.setattr(offline_vm_build_service, 'candidate_commit_is_present', final_output_marker_present)
    now = [0.0]

    def fake_sleep(delay: 'float') -> 'None':
        now[0] += delay
        result_path.write_bytes(b'service-result')
        ready_path.write_bytes(b'service-ready')
        output_state['terminal'] = True
    observed = build.activate_fixed_build_service(invocation, unit_path=tmp_path / 'service.unit', path_unit_path=tmp_path / 'service.path', clock=lambda: now[0], sleep=fake_sleep, poll_interval_seconds=0.1)
    __py_assert1 = observed is candidate
    if not __py_assert1:
        __py_format3 = __pytest_ar._call_reprcompare(('is',), (__py_assert1,), ('%(py0)s is %(py2)s',), (observed, candidate)) % {'py0': __pytest_ar._saferepr(observed) if 'observed' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(observed) else 'observed', 'py2': __pytest_ar._saferepr(candidate) if 'candidate' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(candidate) else 'candidate'}
        __py_format5 = 'assert %(py4)s' % {'py4': __py_format3}
        raise AssertionError(__pytest_ar._format_explanation(__py_format5))
    __py_assert1 = None
    __py_assert2 = {'commit': True, 'terminal': True}
    __py_assert1 = output_state == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (output_state, __py_assert2)) % {'py0': __pytest_ar._saferepr(output_state) if 'output_state' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(output_state) else 'output_state', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None
    __py_assert0 = now[0]
    __py_assert3 = 0.1
    __py_assert2 = __py_assert0 >= __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('>=',), (__py_assert2,), ('%(py1)s >= %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_service_result_requires_matching_final_ready_receipt(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    scratch, invocation, _state_value = executor_case
    result_path = scratch / 'build-service-result.json'
    ready_path = scratch / 'build-service-result-ready.json'
    started_path = scratch / 'build-service-started.json'
    monkeypatch.setattr(build, 'BUILD_RESULT_PATH', str(result_path))
    monkeypatch.setattr(build, 'BUILD_RESULT_READY_PATH', str(ready_path))
    monkeypatch.setattr(build, 'BUILD_SERVICE_STARTED_PATH', str(started_path))
    _private_file(started_path, build.canonical_json({'format': build.BUILD_RECEIPT_FORMAT, 'operation_id': invocation.operation_id, 'request_sha256': invocation.request_sha256}), 384)
    from usb_builder import offline_vm_build_service
    result = build.GuestBuildResult(status='INCOMPLETE_BUILD_EXECUTION', operation_id=invocation.operation_id, request_sha256=invocation.request_sha256, iso_path=None, iso_size=0, iso_sha256=None, command_argv_sha256=build._invocation_sha256(invocation), elapsed_seconds=1.0, commands_run=(build.BUILD_SERVICE_NAME,), cleanup_complete=True)
    offline_vm_build_service._write_result(result)
    with pytest.raises(build.OfflineVmBuildError, match='final-ready receipt'):
        build._load_service_result(invocation)
    offline_vm_build_service._write_result_ready(result)
    __py_assert1 = build._load_service_result
    __py_assert4 = __py_assert1(invocation)
    __py_assert6 = __py_assert4 == result
    if not __py_assert6:
        __py_format8 = __pytest_ar._call_reprcompare(('==',), (__py_assert6,), ('%(py5)s\n{%(py5)s = %(py2)s\n{%(py2)s = %(py0)s._load_service_result\n}(%(py3)s)\n} == %(py7)s',), (__py_assert4, result)) % {'py0': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(invocation) if 'invocation' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(invocation) else 'invocation', 'py5': __pytest_ar._saferepr(__py_assert4), 'py7': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result'}
        __py_format10 = 'assert %(py9)s' % {'py9': __py_format8}
        raise AssertionError(__pytest_ar._format_explanation(__py_format10))
    __py_assert1 = __py_assert4 = __py_assert6 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_candidate_service_result_roundtrips_the_observed_build_process_result(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    scratch, invocation, _state_value = executor_case
    result_path = scratch / 'build-service-result.json'
    ready_path = scratch / 'build-service-result-ready.json'
    started_path = scratch / 'build-service-started.json'
    monkeypatch.setattr(build, 'BUILD_RESULT_PATH', str(result_path))
    monkeypatch.setattr(build, 'BUILD_RESULT_READY_PATH', str(ready_path))
    monkeypatch.setattr(build, 'BUILD_SERVICE_STARTED_PATH', str(started_path))
    _private_file(started_path, build.canonical_json({'format': build.BUILD_RECEIPT_FORMAT, 'operation_id': invocation.operation_id, 'request_sha256': invocation.request_sha256}), 384)
    from usb_builder import offline_vm_build_service
    candidate = build.GuestBuildResult(status='CANDIDATE_ISO_PENDING_HOST_VALIDATION', operation_id=invocation.operation_id, request_sha256=invocation.request_sha256, iso_path=f'{build.WORKER_OUTPUT}/candidate.iso', iso_size=36864, iso_sha256='a' * 64, command_argv_sha256=build._invocation_sha256(invocation), elapsed_seconds=1.0, commands_run=(build.BUILD_SERVICE_NAME,), cleanup_complete=True, build_process_result=_successful_process_result(invocation))
    offline_vm_build_service._write_result(candidate)
    offline_vm_build_service._write_result_ready(candidate)
    assert build._load_service_result(invocation) == candidate

@pytest.mark.parametrize('mutation', ('missing-process', 'child-exit', 'pacstrap-exit', 'timeout', 'nnp-drift', 'namespace-drift', 'capability-drift', 'extra-command', 'reordered-command'))
@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_candidate_service_result_rejects_invalid_observation_or_command_list(mutation: 'str', executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    scratch, invocation, _state_value = executor_case
    result_path = scratch / 'build-service-result.json'
    ready_path = scratch / 'build-service-result-ready.json'
    started_path = scratch / 'build-service-started.json'
    monkeypatch.setattr(build, 'BUILD_RESULT_PATH', str(result_path))
    monkeypatch.setattr(build, 'BUILD_RESULT_READY_PATH', str(ready_path))
    monkeypatch.setattr(build, 'BUILD_SERVICE_STARTED_PATH', str(started_path))
    _private_file(started_path, build.canonical_json({'format': build.BUILD_RECEIPT_FORMAT, 'operation_id': invocation.operation_id, 'request_sha256': invocation.request_sha256}), 384)
    from usb_builder import offline_vm_build_service
    process_result = _successful_process_result(invocation)
    candidate = build.GuestBuildResult(status='CANDIDATE_ISO_PENDING_HOST_VALIDATION', operation_id=invocation.operation_id, request_sha256=invocation.request_sha256, iso_path=f'{build.WORKER_OUTPUT}/candidate.iso', iso_size=36864, iso_sha256='a' * 64, command_argv_sha256=build._invocation_sha256(invocation), elapsed_seconds=1.0, commands_run=(build.BUILD_SERVICE_NAME,), cleanup_complete=True, build_process_result=process_result)
    if mutation == 'missing-process':
        candidate = replace(candidate, build_process_result=None)
    elif mutation == 'child-exit':
        candidate = replace(candidate, build_process_result=replace(process_result, returncode=1))
    elif mutation == 'pacstrap-exit':
        candidate = replace(candidate, build_process_result=replace(process_result, pacstrap_exit_codes=(0, 9)))
    elif mutation == 'timeout':
        candidate = replace(candidate, build_process_result=replace(process_result, timed_out=True))
    elif mutation == 'nnp-drift':
        candidate = replace(candidate, build_process_result=replace(process_result, no_new_privs_during=True))
    elif mutation == 'namespace-drift':
        candidate = replace(candidate, build_process_result=replace(process_result, network_namespace_during='net:[4026533001]'))
    elif mutation == 'capability-drift':
        candidate = replace(candidate, build_process_result=replace(process_result, cap_effective='0000000000000001'))
    elif mutation == 'extra-command':
        candidate = replace(candidate, commands_run=(build.BUILD_SERVICE_NAME, 'pacstrap'))
    else:
        candidate = replace(candidate, commands_run=('pacstrap', build.BUILD_SERVICE_NAME))
    offline_vm_build_service._write_result(candidate)
    offline_vm_build_service._write_result_ready(candidate)
    with pytest.raises(build.OfflineVmBuildError):
        build._load_service_result(invocation)

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_service_result_is_published_complete_and_new_only(monkeypatch: 'pytest.MonkeyPatch', tmp_path: 'Path') -> 'None':
    from usb_builder import offline_vm_build_service
    path = tmp_path / 'build-result.json'
    monkeypatch.setattr(build, 'BUILD_RESULT_PATH', str(path))
    record = build.GuestBuildResult(status='INCOMPLETE_BUILD_EXECUTION', operation_id='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', iso_path=None, iso_size=0, iso_sha256=None, command_argv_sha256='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', elapsed_seconds=2.5, commands_run=(build.BUILD_SERVICE_NAME,), cleanup_complete=True)
    offline_vm_build_service._write_result(record)
    first = path.read_bytes()
    __py_assert0 = b'"status":"INCOMPLETE_BUILD_EXECUTION"'
    __py_assert2 = __py_assert0 in first
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, first)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(first) if 'first' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(first) else 'first'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert1 = path.with_name
    __py_assert4 = path.name
    __py_assert6 = '.tmp'
    __py_assert8 = __py_assert4 + __py_assert6
    __py_assert9 = __py_assert1(__py_assert8)
    __py_assert11 = __py_assert9.exists
    __py_assert13 = __py_assert11()
    __py_assert15 = not __py_assert13
    if not __py_assert15:
        __py_format16 = 'assert not %(py14)s\n{%(py14)s = %(py12)s\n{%(py12)s = %(py10)s\n{%(py10)s = %(py2)s\n{%(py2)s = %(py0)s.with_name\n}((%(py5)s\n{%(py5)s = %(py3)s.name\n} + %(py7)s))\n}.exists\n}()\n}' % {'py0': __pytest_ar._saferepr(path) if 'path' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(path) else 'path', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(path) if 'path' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(path) else 'path', 'py5': __pytest_ar._saferepr(__py_assert4), 'py7': __pytest_ar._saferepr(__py_assert6), 'py10': __pytest_ar._saferepr(__py_assert9), 'py12': __pytest_ar._saferepr(__py_assert11), 'py14': __pytest_ar._saferepr(__py_assert13)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format16))
    __py_assert1 = __py_assert4 = __py_assert6 = __py_assert8 = __py_assert9 = __py_assert11 = __py_assert13 = __py_assert15 = None
    if os.name == 'posix':
        info = path.stat()
        __py_assert1 = []
        __py_assert3 = stat.S_IMODE
        __py_assert6 = info.st_mode
        __py_assert8 = __py_assert3(__py_assert6)
        __py_assert11 = 384
        __py_assert10 = __py_assert8 == __py_assert11
        __py_assert0 = __py_assert10
        if __py_assert10:
            __py_assert17 = info.st_nlink
            __py_assert20 = 1
            __py_assert19 = __py_assert17 == __py_assert20
            __py_assert0 = __py_assert19
        if not __py_assert0:
            __py_format13 = __pytest_ar._call_reprcompare(('==',), (__py_assert10,), ('%(py9)s\n{%(py9)s = %(py4)s\n{%(py4)s = %(py2)s.S_IMODE\n}(%(py7)s\n{%(py7)s = %(py5)s.st_mode\n})\n} == %(py12)s',), (__py_assert8, __py_assert11)) % {'py2': __pytest_ar._saferepr(stat) if 'stat' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(stat) else 'stat', 'py4': __pytest_ar._saferepr(__py_assert3), 'py5': __pytest_ar._saferepr(info) if 'info' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(info) else 'info', 'py7': __pytest_ar._saferepr(__py_assert6), 'py9': __pytest_ar._saferepr(__py_assert8), 'py12': __pytest_ar._saferepr(__py_assert11)}
            __py_format15 = '%(py14)s' % {'py14': __py_format13}
            __py_assert1.append(__py_format15)
            if __py_assert10:
                __py_format22 = __pytest_ar._call_reprcompare(('==',), (__py_assert19,), ('%(py18)s\n{%(py18)s = %(py16)s.st_nlink\n} == %(py21)s',), (__py_assert17, __py_assert20)) % {'py16': __pytest_ar._saferepr(info) if 'info' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(info) else 'info', 'py18': __pytest_ar._saferepr(__py_assert17), 'py21': __pytest_ar._saferepr(__py_assert20)}
                __py_format24 = '%(py23)s' % {'py23': __py_format22}
                __py_assert1.append(__py_format24)
            __py_format25 = __pytest_ar._format_boolop(__py_assert1, 0) % {}
            __py_format27 = 'assert %(py26)s' % {'py26': __py_format25}
            raise AssertionError(__pytest_ar._format_explanation(__py_format27))
        __py_assert0 = __py_assert1 = __py_assert3 = __py_assert6 = __py_assert8 = __py_assert10 = __py_assert11 = __py_assert17 = __py_assert19 = __py_assert20 = None
    with pytest.raises(build.OfflineVmBuildError, match='not a fresh'):
        offline_vm_build_service._write_result(record)
    __py_assert1 = path.read_bytes
    __py_assert3 = __py_assert1()
    __py_assert5 = __py_assert3 == first
    if not __py_assert5:
        __py_format7 = __pytest_ar._call_reprcompare(('==',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.read_bytes\n}()\n} == %(py6)s',), (__py_assert3, first)) % {'py0': __pytest_ar._saferepr(path) if 'path' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(path) else 'path', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3), 'py6': __pytest_ar._saferepr(first) if 'first' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(first) else 'first'}
        __py_format9 = 'assert %(py8)s' % {'py8': __py_format7}
        raise AssertionError(__pytest_ar._format_explanation(__py_format9))
    __py_assert1 = __py_assert3 = __py_assert5 = None

def test_repository_database_reader_is_bounded_and_requires_package_identity(tmp_path: 'Path') -> 'None':
    database = tmp_path / 'core.db'
    desc = b'%NAME%\nbash\n\n%VERSION%\n5.3.20-1\n'
    with tarfile.open(database, mode='w') as archive:
        info = tarfile.TarInfo('bash-5.3.20-1/desc')
        info.size = len(desc)
        archive.addfile(info, io.BytesIO(desc))
    __py_assert1 = build.LinuxGuestBuildOperations
    __py_assert3 = __py_assert1.repository_database_versions
    __py_assert6 = __py_assert3(database)
    __py_assert9 = {'bash': '5.3.20-1'}
    __py_assert8 = __py_assert6 == __py_assert9
    if not __py_assert8:
        __py_format11 = __pytest_ar._call_reprcompare(('==',), (__py_assert8,), ('%(py7)s\n{%(py7)s = %(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.LinuxGuestBuildOperations\n}.repository_database_versions\n}(%(py5)s)\n} == %(py10)s',), (__py_assert6, __py_assert9)) % {'py0': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3), 'py5': __pytest_ar._saferepr(database) if 'database' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(database) else 'database', 'py7': __pytest_ar._saferepr(__py_assert6), 'py10': __pytest_ar._saferepr(__py_assert9)}
        __py_format13 = 'assert %(py12)s' % {'py12': __py_format11}
        raise AssertionError(__pytest_ar._format_explanation(__py_format13))
    __py_assert1 = __py_assert3 = __py_assert6 = __py_assert8 = __py_assert9 = None
    malformed = tmp_path / 'malformed.db'
    malformed.write_bytes(b'not a package database')
    with pytest.raises(build.OfflineVmBuildError, match='repository database'):
        build.LinuxGuestBuildOperations.repository_database_versions(malformed)

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_pacstrap_staging_binds_hash_and_rejects_changed_bytes(tmp_path: 'Path', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    overlay = tmp_path / 'overlay'
    overlay.mkdir(mode=448)
    source = tmp_path / 'pacstrap'
    source.write_bytes(b'patched trusted pacstrap')
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = overlay / 'pacstrap.real'
    monkeypatch.setattr(build, 'WORKER_PACSTRAP_BIN', str(overlay))
    operations = build.LinuxGuestBuildOperations(_state())
    __py_assert1 = operations.stage_pacstrap
    __py_assert6 = __py_assert1(source, destination, expected)
    __py_assert8 = __py_assert6 == expected
    if not __py_assert8:
        __py_format10 = __pytest_ar._call_reprcompare(('==',), (__py_assert8,), ('%(py7)s\n{%(py7)s = %(py2)s\n{%(py2)s = %(py0)s.stage_pacstrap\n}(%(py3)s, %(py4)s, %(py5)s)\n} == %(py9)s',), (__py_assert6, expected)) % {'py0': __pytest_ar._saferepr(operations) if 'operations' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(operations) else 'operations', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(source) if 'source' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(source) else 'source', 'py4': __pytest_ar._saferepr(destination) if 'destination' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(destination) else 'destination', 'py5': __pytest_ar._saferepr(expected) if 'expected' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(expected) else 'expected', 'py7': __pytest_ar._saferepr(__py_assert6), 'py9': __pytest_ar._saferepr(expected) if 'expected' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(expected) else 'expected'}
        __py_format12 = 'assert %(py11)s' % {'py11': __py_format10}
        raise AssertionError(__pytest_ar._format_explanation(__py_format12))
    __py_assert1 = __py_assert6 = __py_assert8 = None
    __py_assert1 = destination.read_bytes
    __py_assert3 = __py_assert1()
    __py_assert7 = source.read_bytes
    __py_assert9 = __py_assert7()
    __py_assert5 = __py_assert3 == __py_assert9
    if not __py_assert5:
        __py_format11 = __pytest_ar._call_reprcompare(('==',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.read_bytes\n}()\n} == %(py10)s\n{%(py10)s = %(py8)s\n{%(py8)s = %(py6)s.read_bytes\n}()\n}',), (__py_assert3, __py_assert9)) % {'py0': __pytest_ar._saferepr(destination) if 'destination' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(destination) else 'destination', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3), 'py6': __pytest_ar._saferepr(source) if 'source' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(source) else 'source', 'py8': __pytest_ar._saferepr(__py_assert7), 'py10': __pytest_ar._saferepr(__py_assert9)}
        __py_format13 = 'assert %(py12)s' % {'py12': __py_format11}
        raise AssertionError(__pytest_ar._format_explanation(__py_format13))
    __py_assert1 = __py_assert3 = __py_assert5 = __py_assert7 = __py_assert9 = None
    destination.unlink()
    source.write_bytes(b'different pacstrap source')
    with pytest.raises(build.OfflineVmBuildError, match='source changed'):
        operations.stage_pacstrap(source, destination, expected)
    __py_assert1 = destination.exists
    __py_assert3 = __py_assert1()
    __py_assert5 = not __py_assert3
    if not __py_assert5:
        __py_format6 = 'assert not %(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.exists\n}()\n}' % {'py0': __pytest_ar._saferepr(destination) if 'destination' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(destination) else 'destination', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert3 = __py_assert5 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_authorized_execution_returns_only_pending_candidate_and_cleans_overlay(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]') -> 'None':
    scratch, invocation, state = executor_case
    operations = build.FixedMkarchisoOperations(state, popen=_fake_popen_factory(scratch))
    result = build.execute_authorized_guest_build(invocation, state, operations)
    __py_assert1 = result.status
    __py_assert4 = 'CANDIDATE_ISO_PENDING_HOST_VALIDATION'
    __py_assert3 = __py_assert1 == __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.status\n} == %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = result.cleanup_complete
    __py_assert4 = True
    __py_assert3 = __py_assert1 is __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('is',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.cleanup_complete\n} is %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = result.iso_path
    __py_assert6 = build.WORKER_OUTPUT
    __py_assert8 = Path(__py_assert6)
    __py_assert10 = 'librecore-v1.iso'
    __py_assert12 = __py_assert8 / __py_assert10
    __py_assert13 = __py_assert12.as_posix
    __py_assert15 = __py_assert13()
    __py_assert3 = __py_assert1 == __py_assert15
    if not __py_assert3:
        __py_format17 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.iso_path\n} == %(py16)s\n{%(py16)s = %(py14)s\n{%(py14)s = (%(py9)s\n{%(py9)s = %(py4)s(%(py7)s\n{%(py7)s = %(py5)s.WORKER_OUTPUT\n})\n} / %(py11)s).as_posix\n}()\n}',), (__py_assert1, __py_assert15)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(Path) if 'Path' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(Path) else 'Path', 'py5': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py7': __pytest_ar._saferepr(__py_assert6), 'py9': __pytest_ar._saferepr(__py_assert8), 'py11': __pytest_ar._saferepr(__py_assert10), 'py14': __pytest_ar._saferepr(__py_assert13), 'py16': __pytest_ar._saferepr(__py_assert15)}
        __py_format19 = 'assert %(py18)s' % {'py18': __py_format17}
        raise AssertionError(__pytest_ar._format_explanation(__py_format19))
    __py_assert1 = __py_assert3 = __py_assert6 = __py_assert8 = __py_assert10 = __py_assert12 = __py_assert13 = __py_assert15 = None
    __py_assert1 = result.iso_size
    __py_assert4 = 18
    __py_assert6 = 2048
    __py_assert8 = __py_assert4 * __py_assert6
    __py_assert3 = __py_assert1 == __py_assert8
    if not __py_assert3:
        __py_format9 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.iso_size\n} == (%(py5)s * %(py7)s)',), (__py_assert1, __py_assert8)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format11 = 'assert %(py10)s' % {'py10': __py_format9}
        raise AssertionError(__pytest_ar._format_explanation(__py_format11))
    __py_assert1 = __py_assert3 = __py_assert4 = __py_assert6 = __py_assert8 = None
    __py_assert1 = result.iso_sha256
    __py_assert5 = hashlib.sha256
    __py_assert7 = b'I'
    __py_assert9 = 18
    __py_assert11 = 2048
    __py_assert13 = __py_assert9 * __py_assert11
    __py_assert14 = __py_assert7 * __py_assert13
    __py_assert15 = __py_assert5(__py_assert14)
    __py_assert17 = __py_assert15.hexdigest
    __py_assert19 = __py_assert17()
    __py_assert3 = __py_assert1 == __py_assert19
    if not __py_assert3:
        __py_format21 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.iso_sha256\n} == %(py20)s\n{%(py20)s = %(py18)s\n{%(py18)s = %(py16)s\n{%(py16)s = %(py6)s\n{%(py6)s = %(py4)s.sha256\n}((%(py8)s * (%(py10)s * %(py12)s)))\n}.hexdigest\n}()\n}',), (__py_assert1, __py_assert19)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(hashlib) if 'hashlib' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(hashlib) else 'hashlib', 'py6': __pytest_ar._saferepr(__py_assert5), 'py8': __pytest_ar._saferepr(__py_assert7), 'py10': __pytest_ar._saferepr(__py_assert9), 'py12': __pytest_ar._saferepr(__py_assert11), 'py16': __pytest_ar._saferepr(__py_assert15), 'py18': __pytest_ar._saferepr(__py_assert17), 'py20': __pytest_ar._saferepr(__py_assert19)}
        __py_format23 = 'assert %(py22)s' % {'py22': __py_format21}
        raise AssertionError(__pytest_ar._format_explanation(__py_format23))
    __py_assert1 = __py_assert3 = __py_assert5 = __py_assert7 = __py_assert9 = __py_assert11 = __py_assert13 = __py_assert14 = __py_assert15 = __py_assert17 = __py_assert19 = None
    __py_assert1 = 'bin'
    __py_assert3 = scratch / __py_assert1
    __py_assert4 = __py_assert3.exists
    __py_assert6 = __py_assert4()
    __py_assert8 = not __py_assert6
    if not __py_assert8:
        __py_format9 = 'assert not %(py7)s\n{%(py7)s = %(py5)s\n{%(py5)s = (%(py0)s / %(py2)s).exists\n}()\n}' % {'py0': __pytest_ar._saferepr(scratch) if 'scratch' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(scratch) else 'scratch', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4), 'py7': __pytest_ar._saferepr(__py_assert6)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format9))
    __py_assert1 = __py_assert3 = __py_assert4 = __py_assert6 = __py_assert8 = None
    __py_assert1 = result.status
    __py_assert4 = 'COMPLETE'
    __py_assert3 = __py_assert1 != __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('!=',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.status\n} != %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker file ownership and mode checks require POSIX')
def test_pacstrap_failure_cannot_be_masked_by_zero_mkarchiso_status(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]') -> 'None':
    scratch, invocation, state = executor_case
    operations = build.FixedMkarchisoOperations(state, popen=_fake_popen_factory(scratch, builder_rc=0, pacstrap_codes=b'0\n7\n'))
    result = build.execute_authorized_guest_build(invocation, state, operations)
    __py_assert1 = result.status
    __py_assert4 = 'INCOMPLETE_BUILD_EXECUTION'
    __py_assert3 = __py_assert1 == __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.status\n} == %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = []
    __py_assert3 = result.iso_path
    __py_assert6 = None
    __py_assert5 = __py_assert3 is __py_assert6
    __py_assert0 = __py_assert5
    if __py_assert5:
        __py_assert12 = result.iso_sha256
        __py_assert15 = None
        __py_assert14 = __py_assert12 is __py_assert15
        __py_assert0 = __py_assert14
    if not __py_assert0:
        __py_format8 = __pytest_ar._call_reprcompare(('is',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s.iso_path\n} is %(py7)s',), (__py_assert3, __py_assert6)) % {'py2': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py4': __pytest_ar._saferepr(__py_assert3), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format10 = '%(py9)s' % {'py9': __py_format8}
        __py_assert1.append(__py_format10)
        if __py_assert5:
            __py_format17 = __pytest_ar._call_reprcompare(('is',), (__py_assert14,), ('%(py13)s\n{%(py13)s = %(py11)s.iso_sha256\n} is %(py16)s',), (__py_assert12, __py_assert15)) % {'py11': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py13': __pytest_ar._saferepr(__py_assert12), 'py16': __pytest_ar._saferepr(__py_assert15)}
            __py_format19 = '%(py18)s' % {'py18': __py_format17}
            __py_assert1.append(__py_format19)
        __py_format20 = __pytest_ar._format_boolop(__py_assert1, 0) % {}
        __py_format22 = 'assert %(py21)s' % {'py21': __py_format20}
        raise AssertionError(__pytest_ar._format_explanation(__py_format22))
    __py_assert0 = __py_assert1 = __py_assert3 = __py_assert5 = __py_assert6 = __py_assert12 = __py_assert14 = __py_assert15 = None
    __py_assert1 = result.cleanup_complete
    __py_assert4 = True
    __py_assert3 = __py_assert1 is __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('is',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.cleanup_complete\n} is %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = 'bin'
    __py_assert3 = scratch / __py_assert1
    __py_assert4 = __py_assert3.exists
    __py_assert6 = __py_assert4()
    __py_assert8 = not __py_assert6
    if not __py_assert8:
        __py_format9 = 'assert not %(py7)s\n{%(py7)s = %(py5)s\n{%(py5)s = (%(py0)s / %(py2)s).exists\n}()\n}' % {'py0': __pytest_ar._saferepr(scratch) if 'scratch' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(scratch) else 'scratch', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4), 'py7': __pytest_ar._saferepr(__py_assert6)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format9))
    __py_assert1 = __py_assert3 = __py_assert4 = __py_assert6 = __py_assert8 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed worker process-group contract requires POSIX')
def test_builder_timeout_is_incomplete_and_kills_process_group(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    scratch, invocation, state = executor_case
    process = _FakeProcess(-9, timeout=True)
    calls = []

    def popen(_argv: 'tuple[str, ...]', **_kwargs: 'Any') -> '_FakeProcess':
        calls.append('popen')
        return process
    killed = []

    def terminate(proc: '_FakeProcess') -> 'None':
        killed.append(proc.pid)
        proc.returncode = -9
    monkeypatch.setattr(build, '_terminate_builder_process', terminate)
    operations = build.FixedMkarchisoOperations(state, popen=popen)
    result = build.execute_authorized_guest_build(invocation, state, operations)
    __py_assert2 = ['popen']
    __py_assert1 = calls == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (calls, __py_assert2)) % {'py0': __pytest_ar._saferepr(calls) if 'calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(calls) else 'calls', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None
    __py_assert2 = [process.pid]
    __py_assert1 = killed == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (killed, __py_assert2)) % {'py0': __pytest_ar._saferepr(killed) if 'killed' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(killed) else 'killed', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None
    __py_assert1 = result.status
    __py_assert4 = 'INCOMPLETE_BUILD_EXECUTION'
    __py_assert3 = __py_assert1 == __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.status\n} == %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = result.iso_path
    __py_assert4 = None
    __py_assert3 = __py_assert1 is __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('is',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.iso_path\n} is %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = result.cleanup_complete
    __py_assert4 = True
    __py_assert3 = __py_assert1 is __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('is',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.cleanup_complete\n} is %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = 'bin'
    __py_assert3 = scratch / __py_assert1
    __py_assert4 = __py_assert3.exists
    __py_assert6 = __py_assert4()
    __py_assert8 = not __py_assert6
    if not __py_assert8:
        __py_format9 = 'assert not %(py7)s\n{%(py7)s = %(py5)s\n{%(py5)s = (%(py0)s / %(py2)s).exists\n}()\n}' % {'py0': __pytest_ar._saferepr(scratch) if 'scratch' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(scratch) else 'scratch', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4), 'py7': __pytest_ar._saferepr(__py_assert6)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format9))
    __py_assert1 = __py_assert3 = __py_assert4 = __py_assert6 = __py_assert8 = None

def test_cleanup_failure_discards_candidate_claim(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]') -> 'None':
    scratch, invocation, state = executor_case
    operations = build.FixedMkarchisoOperations(state, popen=_fake_popen_factory(scratch))
    operations.cleanup_overlay = lambda _overlay: (_ for _ in ()).throw(build.OfflineVmBuildError('cleanup blocked'))
    result = build.execute_authorized_guest_build(invocation, state, operations)
    __py_assert1 = result.status
    __py_assert4 = 'INCOMPLETE_BUILD_CLEANUP'
    __py_assert3 = __py_assert1 == __py_assert4
    if not __py_assert3:
        __py_format6 = __pytest_ar._call_reprcompare(('==',), (__py_assert3,), ('%(py2)s\n{%(py2)s = %(py0)s.status\n} == %(py5)s',), (__py_assert1, __py_assert4)) % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1), 'py5': __pytest_ar._saferepr(__py_assert4)}
        __py_format8 = 'assert %(py7)s' % {'py7': __py_format6}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert3 = __py_assert4 = None
    __py_assert1 = result.cleanup_complete
    __py_assert3 = not __py_assert1
    if not __py_assert3:
        __py_format4 = 'assert not %(py2)s\n{%(py2)s = %(py0)s.cleanup_complete\n}' % {'py0': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py2': __pytest_ar._saferepr(__py_assert1)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format4))
    __py_assert1 = __py_assert3 = None
    __py_assert1 = []
    __py_assert3 = result.iso_path
    __py_assert6 = None
    __py_assert5 = __py_assert3 is __py_assert6
    __py_assert0 = __py_assert5
    if __py_assert5:
        __py_assert12 = result.iso_sha256
        __py_assert15 = None
        __py_assert14 = __py_assert12 is __py_assert15
        __py_assert0 = __py_assert14
    if not __py_assert0:
        __py_format8 = __pytest_ar._call_reprcompare(('is',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s.iso_path\n} is %(py7)s',), (__py_assert3, __py_assert6)) % {'py2': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py4': __pytest_ar._saferepr(__py_assert3), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format10 = '%(py9)s' % {'py9': __py_format8}
        __py_assert1.append(__py_format10)
        if __py_assert5:
            __py_format17 = __pytest_ar._call_reprcompare(('is',), (__py_assert14,), ('%(py13)s\n{%(py13)s = %(py11)s.iso_sha256\n} is %(py16)s',), (__py_assert12, __py_assert15)) % {'py11': __pytest_ar._saferepr(result) if 'result' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(result) else 'result', 'py13': __pytest_ar._saferepr(__py_assert12), 'py16': __pytest_ar._saferepr(__py_assert15)}
            __py_format19 = '%(py18)s' % {'py18': __py_format17}
            __py_assert1.append(__py_format19)
        __py_format20 = __pytest_ar._format_boolop(__py_assert1, 0) % {}
        __py_format22 = 'assert %(py21)s' % {'py21': __py_format20}
        raise AssertionError(__pytest_ar._format_explanation(__py_format22))
    __py_assert0 = __py_assert1 = __py_assert3 = __py_assert5 = __py_assert6 = __py_assert12 = __py_assert14 = __py_assert15 = None

def test_build_unit_is_packaged_byte_exactly_and_deployment_remains_unavailable() -> 'None':
    service_unit = files('usb_builder').joinpath('systemd/librecore-offline-build.service').read_bytes()
    path_unit = files('usb_builder').joinpath('systemd/librecore-offline-build.path').read_bytes()
    preflight_unit = files('usb_builder').joinpath('systemd/librecore-offline-preflight.service').read_bytes()
    preflight_path_unit = files('usb_builder').joinpath('systemd/librecore-offline-preflight.path').read_bytes()
    __py_assert3 = build.BUILD_SERVICE_UNIT_BYTES
    __py_assert1 = service_unit == __py_assert3
    if not __py_assert1:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py4)s\n{%(py4)s = %(py2)s.BUILD_SERVICE_UNIT_BYTES\n}',), (service_unit, __py_assert3)) % {'py0': __pytest_ar._saferepr(service_unit) if 'service_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(service_unit) else 'service_unit', 'py2': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert1 = __py_assert3 = None
    __py_assert3 = build.BUILD_PATH_UNIT_BYTES
    __py_assert1 = path_unit == __py_assert3
    if not __py_assert1:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py4)s\n{%(py4)s = %(py2)s.BUILD_PATH_UNIT_BYTES\n}',), (path_unit, __py_assert3)) % {'py0': __pytest_ar._saferepr(path_unit) if 'path_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(path_unit) else 'path_unit', 'py2': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert1 = __py_assert3 = None
    __py_assert3 = build.BUILD_PREFLIGHT_UNIT_BYTES
    __py_assert1 = preflight_unit == __py_assert3
    if not __py_assert1:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py4)s\n{%(py4)s = %(py2)s.BUILD_PREFLIGHT_UNIT_BYTES\n}',), (preflight_unit, __py_assert3)) % {'py0': __pytest_ar._saferepr(preflight_unit) if 'preflight_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(preflight_unit) else 'preflight_unit', 'py2': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert1 = __py_assert3 = None
    __py_assert3 = build.BUILD_PREFLIGHT_PATH_UNIT_BYTES
    __py_assert1 = preflight_path_unit == __py_assert3
    if not __py_assert1:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py4)s\n{%(py4)s = %(py2)s.BUILD_PREFLIGHT_PATH_UNIT_BYTES\n}',), (preflight_path_unit, __py_assert3)) % {'py0': __pytest_ar._saferepr(preflight_path_unit) if 'preflight_path_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(preflight_path_unit) else 'preflight_path_unit', 'py2': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert1 = __py_assert3 = None
    __py_assert0 = b'NoNewPrivileges=no\n'
    __py_assert2 = __py_assert0 in service_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, service_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(service_unit) if 'service_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(service_unit) else 'service_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert0 = b'CapabilityBoundingSet=CAP_SETUID CAP_SETGID\n'
    __py_assert2 = __py_assert0 in service_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, service_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(service_unit) if 'service_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(service_unit) else 'service_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert0 = b'NoNewPrivileges=yes\n'
    __py_assert2 = __py_assert0 in preflight_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, preflight_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(preflight_unit) if 'preflight_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(preflight_unit) else 'preflight_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert0 = b'PathExists=/run/librecore-scratch/build-invocation.json\n'
    __py_assert2 = __py_assert0 in path_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, path_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(path_unit) if 'path_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(path_unit) else 'path_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert0 = b'ConditionPathExists=!/run/librecore-scratch/build-service-started.json\n'
    __py_assert2 = __py_assert0 in service_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, service_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(service_unit) if 'service_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(service_unit) else 'service_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert0 = b'ConditionPathExists=!/run/librecore-scratch/build-service-result.json\n'
    __py_assert2 = __py_assert0 in service_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, service_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(service_unit) if 'service_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(service_unit) else 'service_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert0 = b'ConditionPathExists=!/run/librecore-scratch/build-service-result-ready.json\n'
    __py_assert2 = __py_assert0 in service_unit
    if not __py_assert2:
        __py_format4 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py3)s',), (__py_assert0, service_unit)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py3': __pytest_ar._saferepr(service_unit) if 'service_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(service_unit) else 'service_unit'}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert0 = __py_assert2 = None
    __py_assert1 = []
    __py_assert2 = b'SuccessAction=poweroff\n'
    __py_assert4 = __py_assert2 in preflight_unit
    __py_assert0 = __py_assert4
    if __py_assert4:
        __py_assert9 = b'FailureAction=poweroff\n'
        __py_assert11 = __py_assert9 in preflight_unit
        __py_assert0 = __py_assert11
    if not __py_assert0:
        __py_format6 = __pytest_ar._call_reprcompare(('in',), (__py_assert4,), ('%(py3)s in %(py5)s',), (__py_assert2, preflight_unit)) % {'py3': __pytest_ar._saferepr(__py_assert2), 'py5': __pytest_ar._saferepr(preflight_unit) if 'preflight_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(preflight_unit) else 'preflight_unit'}
        __py_format8 = '%(py7)s' % {'py7': __py_format6}
        __py_assert1.append(__py_format8)
        if __py_assert4:
            __py_format13 = __pytest_ar._call_reprcompare(('in',), (__py_assert11,), ('%(py10)s in %(py12)s',), (__py_assert9, preflight_unit)) % {'py10': __pytest_ar._saferepr(__py_assert9), 'py12': __pytest_ar._saferepr(preflight_unit) if 'preflight_unit' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(preflight_unit) else 'preflight_unit'}
            __py_format15 = '%(py14)s' % {'py14': __py_format13}
            __py_assert1.append(__py_format15)
        __py_format16 = __pytest_ar._format_boolop(__py_assert1, 0) % {}
        __py_format18 = 'assert %(py17)s' % {'py17': __py_format16}
        raise AssertionError(__pytest_ar._format_explanation(__py_format18))
    __py_assert0 = __py_assert1 = __py_assert2 = __py_assert4 = __py_assert9 = __py_assert11 = None
    __py_assert1 = build.production_integration_status
    __py_assert3 = __py_assert1()
    __py_assert6 = 'OFFLINE_VM_WORKER_PROTOTYPE_UNAVAILABLE'
    __py_assert5 = __py_assert3 == __py_assert6
    if not __py_assert5:
        __py_format8 = __pytest_ar._call_reprcompare(('==',), (__py_assert5,), ('%(py4)s\n{%(py4)s = %(py2)s\n{%(py2)s = %(py0)s.production_integration_status\n}()\n} == %(py7)s',), (__py_assert3, __py_assert6)) % {'py0': __pytest_ar._saferepr(build) if 'build' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(build) else 'build', 'py2': __pytest_ar._saferepr(__py_assert1), 'py4': __pytest_ar._saferepr(__py_assert3), 'py7': __pytest_ar._saferepr(__py_assert6)}
        __py_format10 = 'assert %(py9)s' % {'py9': __py_format8}
        raise AssertionError(__pytest_ar._format_explanation(__py_format10))
    __py_assert1 = __py_assert3 = __py_assert5 = __py_assert6 = None
    from usb_builder.offline_vm_worker import PRODUCTION_WORKER_DEPLOYMENT
    __py_assert2 = None
    __py_assert1 = PRODUCTION_WORKER_DEPLOYMENT is __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('is',), (__py_assert1,), ('%(py0)s is %(py3)s',), (PRODUCTION_WORKER_DEPLOYMENT, __py_assert2)) % {'py0': __pytest_ar._saferepr(PRODUCTION_WORKER_DEPLOYMENT) if 'PRODUCTION_WORKER_DEPLOYMENT' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(PRODUCTION_WORKER_DEPLOYMENT) else 'PRODUCTION_WORKER_DEPLOYMENT', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None

@pytest.mark.parametrize('relative', ('etc/systemd/system.control/librecore-offline-build.service', 'run/systemd/generator.early/librecore-offline-build.service', 'etc/systemd/system/librecore-offline-build.service'))
def test_effective_systemd_validation_rejects_higher_priority_complete_unit_files(relative: 'str', tmp_path: 'Path') -> 'None':
    path = tmp_path.joinpath(*relative.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'[Service]\nPrivateNetwork=no\n')
    canonical = tmp_path / 'usr' / 'lib' / 'systemd' / 'system' / 'librecore-offline-build.service'
    with pytest.raises(build.OfflineVmBuildError, match='higher-priority systemd unit override'):
        build._verify_no_systemd_override_files('librecore-offline-build.service', canonical, root=tmp_path)

@pytest.mark.parametrize('relative', ('etc/systemd/system.control/librecore-offline-build.service.d/unsafe.conf', 'run/systemd/generator.early/service.d/unsafe.conf', 'etc/systemd/system/librecore-offline-build.service.d/unsafe.conf'))
def test_effective_systemd_validation_rejects_unit_and_generic_service_dropins(relative: 'str', tmp_path: 'Path') -> 'None':
    path = tmp_path.joinpath(*relative.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'[Service]\nNetworkNamespacePath=/run/host-net\n')
    canonical = tmp_path / 'usr' / 'lib' / 'systemd' / 'system' / 'librecore-offline-build.service'
    with pytest.raises(build.OfflineVmBuildError, match='unreviewed systemd drop-in'):
        build._verify_no_systemd_override_files('librecore-offline-build.service', canonical, root=tmp_path)

@pytest.mark.skipif(os.name != 'posix', reason='symlink creation is host-policy-specific')
def test_effective_systemd_validation_rejects_dangling_override_symlink(tmp_path: 'Path') -> 'None':
    directory = tmp_path / 'etc' / 'systemd' / 'system.control'
    directory.mkdir(parents=True)
    target = directory / 'librecore-offline-build.service'
    try:
        target.symlink_to(directory / 'missing-target')
    except OSError as exc:
        pytest.skip(f'host does not permit symlink fixture: {exc}')
    canonical = tmp_path / 'usr' / 'lib' / 'systemd' / 'system' / 'librecore-offline-build.service'
    with pytest.raises(build.OfflineVmBuildError, match='symlink component'):
        build._verify_no_systemd_override_files('librecore-offline-build.service', canonical, root=tmp_path)

def _effective_service_output(fragment_path: 'str'='/synthetic-root/usr/lib/systemd/system/librecore-offline-build.service', **overrides: 'str') -> 'bytes':
    expected = {'LoadState': 'loaded', 'FragmentPath': fragment_path, 'DropInPaths': '', 'NeedDaemonReload': 'no', **build._service_expected_properties(no_new_privileges='no', readonly_paths='/mnt/librecore-source /mnt/librecore-packages /run/librecore-package-dbs'), 'ExecStart': '{ path=/usr/bin/python ; argv[]=/usr/bin/python -B -m usb_builder.offline_vm_build_service ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'}
    expected.update(overrides)
    names = (*build._SYSTEMD_COMMON_PROPERTIES, *build._SYSTEMD_SERVICE_PROPERTIES)
    return ('\n'.join((f'{key}={expected[key]}' for key in names)) + '\n').encode('utf-8')

def test_effective_systemd_properties_are_read_with_fixed_query_and_accept_exact_policy(tmp_path: 'Path') -> 'None':
    root = tmp_path / 'synthetic-root'
    canonical = root / 'usr' / 'lib' / 'systemd' / 'system' / 'librecore-offline-build.service'
    canonical.parent.mkdir(parents=True)
    calls = []

    def query(executable: 'Path', args: 'tuple[str, ...]') -> 'bytes':
        calls.append((executable, args))
        return _effective_service_output(fragment_path=str(canonical))
    expected = {**build._service_expected_properties(no_new_privileges='no', readonly_paths='/mnt/librecore-source /mnt/librecore-packages /run/librecore-package-dbs'), 'ExecStart': '/usr/bin/python -B -m usb_builder.offline_vm_build_service'}
    build._verify_effective_systemd_unit('librecore-offline-build.service', canonical, expected=expected, runner=query, root=root)
    __py_assert2 = [(Path('/usr/bin/systemctl'), ('show', '--no-pager', '--property=' + ','.join((*build._SYSTEMD_COMMON_PROPERTIES, *build._SYSTEMD_SERVICE_PROPERTIES)), 'librecore-offline-build.service'))]
    __py_assert1 = calls == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (calls, __py_assert2)) % {'py0': __pytest_ar._saferepr(calls) if 'calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(calls) else 'calls', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None

@pytest.mark.parametrize(('key', 'value', 'message'), (('NoNewPrivileges', 'yes', 'NoNewPrivileges'), ('PrivateNetwork', 'no', 'PrivateNetwork'), ('NetworkNamespacePath', '/run/host-net', 'NetworkNamespacePath'), ('CapabilityBoundingSet', 'CAP_SYS_ADMIN CAP_SETUID CAP_SETGID', 'CapabilityBoundingSet'), ('DropInPaths', '/etc/systemd/system/override.conf', 'DropInPaths'), ('ExecStart', '{ path=/bin/sh ; argv[]=/bin/sh -c true ; ignore_errors=no ; start_time=[n/a] ; }', 'ExecStart'), ('ExecStart', '{ path=/usr/bin/python ; argv[]=/usr/bin/python -B -m usb_builder.offline_vm_build_service ; ignore_errors=no ; }{ path=/bin/sh ; argv[]=/bin/sh -c true ; ignore_errors=no ; }', 'ExecStart')))
def test_effective_systemd_properties_reject_security_or_command_drift(key: 'str', value: 'str', message: 'str', tmp_path: 'Path') -> 'None':
    root = tmp_path / 'synthetic-root'
    canonical = root / 'usr' / 'lib' / 'systemd' / 'system' / 'librecore-offline-build.service'
    canonical.parent.mkdir(parents=True)
    expected = {**build._service_expected_properties(no_new_privileges='no', readonly_paths='/mnt/librecore-source /mnt/librecore-packages /run/librecore-package-dbs'), 'ExecStart': '/usr/bin/python -B -m usb_builder.offline_vm_build_service'}
    with pytest.raises(build.OfflineVmBuildError, match=message):
        build._verify_effective_systemd_unit('librecore-offline-build.service', canonical, expected=expected, runner=lambda _executable, _args: _effective_service_output(fragment_path=str(canonical), **{key: value}), root=root)

@pytest.mark.parametrize(('verifier_name', 'unit_name', 'resource_name'), (('_verify_installed_service_unit', 'librecore-offline-build.service', 'librecore-offline-build.service'), ('_verify_installed_path_unit', 'librecore-offline-build.path', 'librecore-offline-build.path'), ('_verify_installed_preflight_unit', 'librecore-offline-preflight.service', 'librecore-offline-preflight.service'), ('_verify_installed_preflight_path_unit', 'librecore-offline-preflight.path', 'librecore-offline-preflight.path')))
def test_installed_unit_verifiers_always_check_effective_systemd_policy(verifier_name: 'str', unit_name: 'str', resource_name: 'str', tmp_path: 'Path', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    resource_calls = []
    effective_calls = []

    def resource(path: 'Path', name: 'str', _bytes: 'bytes') -> 'None':
        resource_calls.append((path, name))

    def effective(unit: 'str', path: 'Path', *, expected: 'dict[str, str]') -> 'None':
        effective_calls.append((unit, path, expected))
    monkeypatch.setattr(build, '_verify_installed_systemd_resource', resource)
    monkeypatch.setattr(build, '_verify_effective_systemd_unit', effective)
    target = tmp_path / unit_name
    getattr(build, verifier_name)(target)
    __py_assert2 = [(target, resource_name)]
    __py_assert1 = resource_calls == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (resource_calls, __py_assert2)) % {'py0': __pytest_ar._saferepr(resource_calls) if 'resource_calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(resource_calls) else 'resource_calls', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None
    __py_assert2 = len(effective_calls)
    __py_assert5 = 1
    __py_assert4 = __py_assert2 == __py_assert5
    if not __py_assert4:
        __py_format7 = __pytest_ar._call_reprcompare(('==',), (__py_assert4,), ('%(py3)s\n{%(py3)s = %(py0)s(%(py1)s)\n} == %(py6)s',), (__py_assert2, __py_assert5)) % {'py0': __pytest_ar._saferepr(len) if 'len' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(len) else 'len', 'py1': __pytest_ar._saferepr(effective_calls) if 'effective_calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(effective_calls) else 'effective_calls', 'py3': __pytest_ar._saferepr(__py_assert2), 'py6': __pytest_ar._saferepr(__py_assert5)}
        __py_format9 = 'assert %(py8)s' % {'py8': __py_format7}
        raise AssertionError(__pytest_ar._format_explanation(__py_format9))
    __py_assert2 = __py_assert4 = __py_assert5 = None
    __py_assert0 = effective_calls[0][0:2]
    __py_assert3 = (unit_name, target)
    __py_assert2 = __py_assert0 == __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None
    if unit_name.endswith('.service'):
        __py_assert0 = 'NoNewPrivileges'
        __py_assert3 = effective_calls[0][2]
        __py_assert2 = __py_assert0 in __py_assert3
        if not __py_assert2:
            __py_format5 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
            __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
            raise AssertionError(__pytest_ar._format_explanation(__py_format7))
        __py_assert0 = __py_assert2 = __py_assert3 = None
        __py_assert0 = 'NetworkNamespacePath'
        __py_assert3 = effective_calls[0][2]
        __py_assert2 = __py_assert0 in __py_assert3
        if not __py_assert2:
            __py_format5 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
            __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
            raise AssertionError(__pytest_ar._format_explanation(__py_format7))
        __py_assert0 = __py_assert2 = __py_assert3 = None
        __py_assert0 = 'ExecStart'
        __py_assert3 = effective_calls[0][2]
        __py_assert2 = __py_assert0 in __py_assert3
        if not __py_assert2:
            __py_format5 = __pytest_ar._call_reprcompare(('in',), (__py_assert2,), ('%(py1)s in %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
            __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
            raise AssertionError(__pytest_ar._format_explanation(__py_format7))
        __py_assert0 = __py_assert2 = __py_assert3 = None
        return
    __py_assert0 = effective_calls[0][2]
    __py_assert3 = {'Triggers': unit_name.replace('.path', '.service')}
    __py_assert2 = __py_assert0 == __py_assert3
    if not __py_assert2:
        __py_format5 = __pytest_ar._call_reprcompare(('==',), (__py_assert2,), ('%(py1)s == %(py4)s',), (__py_assert0, __py_assert3)) % {'py1': __pytest_ar._saferepr(__py_assert0), 'py4': __pytest_ar._saferepr(__py_assert3)}
        __py_format7 = 'assert %(py6)s' % {'py6': __py_format5}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert0 = __py_assert2 = __py_assert3 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed Arch package tool paths are Linux-specific')
def test_package_metadata_queries_have_only_fixed_local_command_shapes(monkeypatch: 'pytest.MonkeyPatch', tmp_path: 'Path') -> 'None':
    source = tmp_path / 'package.pkg.tar.zst'
    signature = tmp_path / 'package.pkg.tar.zst.sig'
    source.write_bytes(b'archive')
    signature.write_bytes(b'signature')
    calls = []

    def fake_query(cls: 'type[build.LinuxGuestBuildOperations]', executable: 'Path', args: 'tuple[str, ...]', *, timeout: 'float'=30.0) -> 'bytes':
        calls.append((str(executable), args))
        if executable == Path('/usr/bin/pacman-key'):
            return b'gpg: Good signature from pinned test key\n'
        return b'archiso 91-1\n'
    monkeypatch.setattr(build.LinuxGuestBuildOperations, '_run_fixed_query', classmethod(fake_query))
    operations = build.LinuxGuestBuildOperations(_state())
    __py_assert1 = operations.verify_detached_signature
    __py_assert5 = __py_assert1(signature, source)
    if not __py_assert5:
        __py_format7 = 'assert %(py6)s\n{%(py6)s = %(py2)s\n{%(py2)s = %(py0)s.verify_detached_signature\n}(%(py3)s, %(py4)s)\n}' % {'py0': __pytest_ar._saferepr(operations) if 'operations' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(operations) else 'operations', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(signature) if 'signature' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(signature) else 'signature', 'py4': __pytest_ar._saferepr(source) if 'source' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(source) else 'source', 'py6': __pytest_ar._saferepr(__py_assert5)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format7))
    __py_assert1 = __py_assert5 = None
    __py_assert1 = operations.package_identity
    __py_assert4 = __py_assert1(source)
    __py_assert7 = ('archiso', '91-1')
    __py_assert6 = __py_assert4 == __py_assert7
    if not __py_assert6:
        __py_format9 = __pytest_ar._call_reprcompare(('==',), (__py_assert6,), ('%(py5)s\n{%(py5)s = %(py2)s\n{%(py2)s = %(py0)s.package_identity\n}(%(py3)s)\n} == %(py8)s',), (__py_assert4, __py_assert7)) % {'py0': __pytest_ar._saferepr(operations) if 'operations' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(operations) else 'operations', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(source) if 'source' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(source) else 'source', 'py5': __pytest_ar._saferepr(__py_assert4), 'py8': __pytest_ar._saferepr(__py_assert7)}
        __py_format11 = 'assert %(py10)s' % {'py10': __py_format9}
        raise AssertionError(__pytest_ar._format_explanation(__py_format11))
    __py_assert1 = __py_assert4 = __py_assert6 = __py_assert7 = None
    __py_assert2 = [('/usr/bin/pacman-key', ('--verify', str(signature), str(source))), ('/usr/bin/pacman', ('--config', '/dev/null', '-Qp', str(source)))]
    __py_assert1 = calls == __py_assert2
    if not __py_assert1:
        __py_format4 = __pytest_ar._call_reprcompare(('==',), (__py_assert1,), ('%(py0)s == %(py3)s',), (calls, __py_assert2)) % {'py0': __pytest_ar._saferepr(calls) if 'calls' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(calls) else 'calls', 'py3': __pytest_ar._saferepr(__py_assert2)}
        __py_format6 = 'assert %(py5)s' % {'py5': __py_format4}
        raise AssertionError(__pytest_ar._format_explanation(__py_format6))
    __py_assert1 = __py_assert2 = None
    monkeypatch.setattr(build.LinuxGuestBuildOperations, '_run_fixed_query', classmethod(lambda _cls, _executable, _args, *, timeout=30.0: b'gpg: BAD signature\n'))
    __py_assert1 = operations.verify_detached_signature
    __py_assert5 = __py_assert1(signature, source)
    __py_assert7 = not __py_assert5
    if not __py_assert7:
        __py_format8 = 'assert not %(py6)s\n{%(py6)s = %(py2)s\n{%(py2)s = %(py0)s.verify_detached_signature\n}(%(py3)s, %(py4)s)\n}' % {'py0': __pytest_ar._saferepr(operations) if 'operations' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(operations) else 'operations', 'py2': __pytest_ar._saferepr(__py_assert1), 'py3': __pytest_ar._saferepr(signature) if 'signature' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(signature) else 'signature', 'py4': __pytest_ar._saferepr(source) if 'source' in __py_builtins.locals() or __pytest_ar._should_repr_global_name(source) else 'source', 'py6': __pytest_ar._saferepr(__py_assert5)}
        raise AssertionError(__pytest_ar._format_explanation(__py_format8))
    __py_assert1 = __py_assert5 = __py_assert7 = None

@pytest.mark.skipif(os.name != 'posix', reason='fixed builder process contract requires Linux/POSIX')
def test_authorized_execution_returns_the_actual_bounded_process_observations(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]') -> 'None':
    scratch, invocation, state = executor_case
    operations = build.FixedMkarchisoOperations(
        state,
        popen=_fake_popen_factory(scratch, builder_rc=0, pacstrap_codes=b'0\n7\n'),
    )
    result = build.execute_authorized_guest_build(invocation, state, operations)
    assert result.status == 'INCOMPLETE_BUILD_EXECUTION'
    assert result.build_process_result is not None
    assert result.build_process_result.returncode == 0
    assert result.build_process_result.timed_out is False
    assert result.build_process_result.pacstrap_exit_codes == (0, 7)
    assert result.build_process_result.argv_sha256 == build._invocation_sha256(invocation)

@pytest.mark.skipif(os.name != 'posix', reason='fixed builder process contract requires Linux/POSIX')
def test_authorized_execution_retains_timeout_observation(executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]', monkeypatch: 'pytest.MonkeyPatch') -> 'None':
    _scratch, invocation, state = executor_case
    process = _FakeProcess(-9, timeout=True)
    monkeypatch.setattr(build, '_terminate_builder_process', lambda proc: setattr(proc, 'returncode', -9))
    result = build.execute_authorized_guest_build(
        invocation,
        state,
        build.FixedMkarchisoOperations(state, popen=lambda *_args, **_kwargs: process),
    )
    assert result.status == 'INCOMPLETE_BUILD_EXECUTION'
    assert result.build_process_result is not None
    assert result.build_process_result.returncode == -9
    assert result.build_process_result.timed_out is True

def _package_manifest_for_database_signature_policy(*, database_signature: bool) -> 'dict[str, Any]':
    signature_path: str | None = 'repos/core/os/x86_64/core.db.sig' if database_signature else None
    signature_sha256: str | None = 'a' * 64 if database_signature else None
    return {
        'repository_databases': {
            'core': {
                'path': build.PACMAN_DATABASE_RELATIVE['core'],
                'sha256': 'b' * 64,
                'signature_path': signature_path,
                'signature_sha256': signature_sha256,
            },
            'extra': {
                'path': build.PACMAN_DATABASE_RELATIVE['extra'],
                'sha256': 'c' * 64,
                'signature_path': None,
                'signature_sha256': None,
            },
        },
        'package_closure_path': build.PACKAGE_CLOSURE_RELATIVE,
        'package_closure_sha256': '9' * 64,
        'packages': [{
            'repo': 'core',
            'name': 'fixture',
            'version': '1-1',
            'arch': 'x86_64',
            'filename': 'fixture-1-1-x86_64.pkg.tar.zst',
            'database_sha256': 'b' * 64,
            'archive_path': 'repos/core/os/x86_64/fixture-1-1-x86_64.pkg.tar.zst',
            'archive_size': 1,
            'archive_sha256': 'd' * 64,
            'signature_blob_sha256': 'f' * 64,
            'included_by': ['worker_root'],
            'signature_path': 'repos/core/os/x86_64/fixture-1-1-x86_64.pkg.tar.zst.sig',
            'signature_sha256': 'e' * 64,
        }],
        'pacstrap_overlay': {
            'path': build.PACSTRAP_SOURCE_RELATIVE,
            'source_sha256': 'f' * 64,
            'patch_sha256': '1' * 64,
            'patched_sha256': '2' * 64,
        },
    }

def test_repository_database_signatures_follow_database_optional_policy() -> 'None':
    unsigned = _package_manifest_for_database_signature_policy(database_signature=False)
    expected = build._manifest_file_set(unsigned)
    assert build.PACMAN_DATABASE_RELATIVE['core'] in expected
    assert build.PACMAN_DATABASE_RELATIVE['core'] + '.sig' not in expected
    assert build.PACMAN_DATABASE_RELATIVE['extra'] + '.sig' not in expected

    signed = _package_manifest_for_database_signature_policy(database_signature=True)
    signed_expected = build._manifest_file_set(signed)
    assert signed['repository_databases']['core']['signature_path'] in signed_expected

    malformed = _package_manifest_for_database_signature_policy(database_signature=False)
    malformed['repository_databases']['core']['signature_sha256'] = 'a' * 64
    with pytest.raises(build.OfflineVmBuildError, match='absent optional repository database signature has a hash'):
        build._manifest_file_set(malformed)

def test_repository_database_hashes_are_rechecked_after_builder_use(tmp_path: 'Path') -> 'None':
    manifest = _package_manifest_for_database_signature_policy(database_signature=False)
    database_root = tmp_path / 'repos'
    pins: dict[str, object] = {}
    for repo_name, relative in build.PACMAN_DATABASE_RELATIVE.items():
        path = database_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f'{repo_name}-database'.encode('ascii')
        path.write_bytes(payload)
        digest = build._sha256_bytes(payload)
        manifest['repository_databases'][repo_name]['sha256'] = digest
        if repo_name == 'core':
            manifest['packages'][0]['database_sha256'] = digest
        pins[repo_name] = digest

    class SignatureVerifier:
        @staticmethod
        def verify_detached_signature(_signature: 'Path', _artifact: 'Path') -> 'bool':
            return True

    verified = build._verify_repository_database_inputs(manifest, database_root, SignatureVerifier(), pins)
    assert set(verified) == {'core', 'extra'}

    core_path = database_root / build.PACMAN_DATABASE_RELATIVE['core']
    core_path.write_bytes(b'changed-after-builder')
    with pytest.raises(build.OfflineVmBuildError, match='core database SHA-256 differs from its pinned identity'):
        build._verify_repository_database_inputs(manifest, database_root, SignatureVerifier(), pins)

def test_service_rejects_post_build_database_drift_before_candidate_volume_publication(
    executor_case: 'tuple[Path, build.BuildInvocation, build.GuestSecurityState]',
    monkeypatch: 'pytest.MonkeyPatch',
) -> 'None':
    _scratch, invocation, state = executor_case
    from usb_builder import offline_vm_build_service

    candidate = build.GuestBuildResult(
        status='CANDIDATE_ISO_PENDING_HOST_VALIDATION',
        operation_id=invocation.operation_id,
        request_sha256=invocation.request_sha256,
        iso_path=f'{build.WORKER_OUTPUT}/candidate.iso',
        iso_size=36864,
        iso_sha256='a' * 64,
        command_argv_sha256=build._invocation_sha256(invocation),
        elapsed_seconds=1.0,
        commands_run=(build.BUILD_SERVICE_NAME,),
        cleanup_complete=True,
        build_process_result=_successful_process_result(invocation),
    )
    monkeypatch.setattr(build, '_verify_installed_service_unit', lambda: None)
    monkeypatch.setattr(build, '_verify_installed_path_unit', lambda: None)
    monkeypatch.setattr(offline_vm_build_service, 'load_invocation', lambda: (invocation, {}))
    monkeypatch.setattr(build, '_write_exclusive_private', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(offline_vm_build_service.guest, 'collect_guest_isolation', lambda: object())
    monkeypatch.setattr(offline_vm_build_service.guest, 'validate_guest_isolation', lambda *_args: None)
    monkeypatch.setattr(offline_vm_build_service, 'collect_build_service_security', lambda: state)
    monkeypatch.setattr(build, 'LinuxGuestBuildOperations', lambda _state: object())
    monkeypatch.setattr(build, 'execute_authorized_guest_build', lambda *_args: candidate)

    def reject_changed_databases(*_args: object, **_kwargs: object) -> None:
        raise build.OfflineVmBuildError('database changed after builder use')

    monkeypatch.setattr(build, 'verify_repository_database_inputs_after_build', reject_changed_databases)
    monkeypatch.setattr(
        offline_vm_build_service,
        'write_candidate_result_volume',
        lambda *_args, **_kwargs: pytest.fail('candidate result volume was published after DB drift'),
    )
    assert offline_vm_build_service.main() == 2


@pytest.mark.skipif(os.name != 'posix', reason='pacstrap receipt identity race requires POSIX inode metadata')
def test_pacstrap_receipt_rejects_same_inode_same_size_rewrite_between_stat_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = tmp_path / 'pacstrap-exit-codes'
    receipt.write_bytes(b'0\n')
    receipt.chmod(0o600)
    initial = receipt.lstat()
    real_open = os.open
    changed = False

    def rewrite_then_open(open_path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal changed
        if Path(open_path) == receipt and not changed:
            changed = True
            writer = real_open(receipt, os.O_WRONLY | os.O_TRUNC)
            try:
                os.write(writer, b'1\n')
            finally:
                os.close(writer)
            os.utime(receipt, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))
        return real_open(open_path, flags, *args, **kwargs)

    monkeypatch.setattr(build.os, 'open', rewrite_then_open)
    observed = build._read_pacstrap_exit_code_snapshot(receipt, initial)

    after = receipt.lstat()
    assert changed
    assert (after.st_dev, after.st_ino, after.st_size) == (initial.st_dev, initial.st_ino, initial.st_size)
    assert after.st_mtime_ns != initial.st_mtime_ns
    assert observed == (-1,)


@pytest.mark.skipif(os.name != 'posix', reason='pacstrap receipt identity and mode checks require POSIX')
@pytest.mark.parametrize(
    ('receipt_bytes', 'expected'),
    (
        (b'0\n', (0,)),
        (b'0\n0\n', (0, 0)),
        (b'0', (-1,)),
        (b'0\r\n', (-1,)),
        (b'0\v', (-1,)),
        (b'00\n', (-1,)),
        (b'0\n\n', (-1,)),
    ),
)
def test_pacstrap_receipt_requires_complete_canonical_lf_rows(
    receipt_bytes: bytes,
    expected: tuple[int, ...],
    tmp_path: Path,
) -> None:
    receipt = tmp_path / 'pacstrap-exit-codes'
    receipt.write_bytes(receipt_bytes)
    receipt.chmod(0o600)

    observed = build._read_pacstrap_exit_code_snapshot(receipt, receipt.lstat())

    assert observed == expected
