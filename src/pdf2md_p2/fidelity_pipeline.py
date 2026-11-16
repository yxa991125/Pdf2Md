"""Publish F1 after raw conversion, retaining both coherent directory versions."""

from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import time

from .common import implementation, read, save, sha, verify
from .fidelity_result import validate_result
from .fidelity_sandbox import command


def transaction_path(output):
    return output.parent / ('.'+output.name+'.fidelity-convert')


@contextmanager
def pipeline_lock(output):
    import fcntl
    output.parent.mkdir(parents=True, exist_ok=True)
    path = output.parent / ('.'+output.name+'.pipeline.lock')
    if path.is_symlink():
        raise ValueError('Pipeline lock must not be a symlink')
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another conversion is writing this destination') from error
        yield


def recover(output, pdf, resume):
    transaction = transaction_path(output)
    state_file = transaction / 'pipeline.json'
    if transaction.is_symlink() or state_file.is_symlink():
        raise ValueError('Pipeline transaction must not be a symlink')
    if not state_file.is_file():
        return
    state = read(state_file)
    if state.get('status') != 'EXCHANGING':
        return
    if not resume or state['binding']['input_sha256'] != sha(pdf):
        raise ValueError('Interrupted publication requires matching input and explicit --resume')
    if output.exists():
        seal = output / 'run_manifest.json'
        if not seal.is_file() or sha(seal) not in (state['raw_seal_sha256'], state['new_seal_sha256']):
            raise ValueError('Publication destination differs from both recorded versions')
        state['status'] = 'PUBLISHED' if sha(seal) == state['new_seal_sha256'] else 'RAW_RESTORED'
    else:
        raw = transaction / 'R0'
        if not raw.is_dir() or sha(raw / 'run_manifest.json') != state['raw_seal_sha256']:
            raise ValueError('Recorded raw result is unavailable for publication recovery')
        verify(raw)
        os.rename(raw, output)
        state['status'] = 'RAW_RESTORED'
    save(state_file, state)


def exchange(output, candidate, transaction):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is not None:
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if not rename(-100, os.fsencode(output), -100, os.fsencode(candidate), 2):
            return 'ATOMIC_DIRECTORY_EXCHANGE', candidate
        error = ctypes.get_errno()
        if error not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise OSError(error, os.strerror(error), str(output))
    raw = transaction / 'R0'
    if raw.exists():
        raise ValueError('Raw preservation destination already exists')
    os.rename(output, raw)
    try:
        os.rename(candidate, output)
    except BaseException:
        if not output.exists():
            os.rename(raw, output)
        raise
    return 'JOURNALED_DIRECTORY_RENAMES', raw


def finish(config, pdf, output, mode):
    if mode not in ('check', 'repair'):
        raise ValueError('Unsupported fidelity mode')
    if (output / 'fidelity_binding.json').is_file():
        summary = validate_result(output)
        if read(output / 'fidelity_binding.json')['mode'] != mode:
            raise ValueError('Completed fidelity mode differs from requested mode')
        return {'status': 'REUSED_VERIFIED', 'fidelity': summary}
    started = time.monotonic()
    verify(output)
    original_configuration = read(output / 'configuration.json')
    binding = {'input_sha256': sha(pdf), 'configuration': original_configuration,
               'implementation': implementation(), 'mode': mode}
    if original_configuration.get('fidelity_mode') != mode:
        raise ValueError('Raw conversion is not bound to the requested fidelity mode')
    transaction = transaction_path(output)
    if transaction.is_symlink():
        raise ValueError('Pipeline transaction must not be a symlink')
    transaction.mkdir(exist_ok=True)
    state_file = transaction / 'pipeline.json'
    if state_file.exists() and read(state_file)['binding'] != binding:
        raise ValueError('Fidelity transaction inputs or implementation changed')
    state = {'status': 'PREPARING', 'binding': binding}
    save(state_file, state)
    argv = command(config, pdf, output, transaction, mode)
    with (transaction / 'fidelity.stdout.log').open('w') as stdout, (transaction / 'fidelity.stderr.log').open('w') as stderr:
        result = subprocess.run(argv, stdout=stdout, stderr=stderr)
    if result.returncode:
        save(state_file, {**state, 'status': 'FAILED_RAW_RESULT_RETAINED', 'returncode': result.returncode})
        raise ValueError('Fidelity failed; the complete raw result is retained. Logs: '+str(transaction))
    candidate = transaction / 'R1'
    verify(candidate)
    summary = validate_result(candidate)
    if read(candidate / 'configuration.json') != original_configuration:
        raise ValueError('Fidelity publication changed conversion/resume binding')
    state.update(status='EXCHANGING', raw_seal_sha256=sha(output / 'run_manifest.json'),
                 new_seal_sha256=sha(candidate / 'run_manifest.json'), command=argv)
    save(state_file, state)
    strategy, raw = exchange(output, candidate, transaction)
    state.update(status='PUBLISHED', strategy=strategy, raw_result=str(raw),
                 seconds=time.monotonic()-started)
    save(state_file, state)
    return {'status': 'PUBLISHED', 'fidelity': summary, 'receipt': str(state_file)}
