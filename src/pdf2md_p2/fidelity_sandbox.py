"""CPU-only source postprocessing in the existing locked coordinator environment."""

import os
from pathlib import Path
import socket

from .common import PRODUCT
from .sandbox import base


def command(config, pdf, original, transaction, mode):
    source_only = {**config, 'resolved_compute': 'cpu', 'device_files': [],
                   'cpu_python': config['coordinator_python']}
    return [*base(source_only), '--ro-bind', str(PRODUCT), '/product',
        '--ro-bind', str(pdf), '/input.pdf', '--ro-bind', str(original), '/original',
        '--bind', str(transaction), '/transaction',
        '--setenv', 'P2_ISOLATED', '1', '--setenv', 'PDF2MD_INLINE_FIDELITY', '1',
        '--setenv', 'PDF2MD_FIDELITY_PARENT_PID_NS', os.readlink('/proc/self/ns/pid'),
        '--chdir', '/product', config['coordinator_python'], '-B', '-m', 'pdf2md_p2', 'fidelity',
        '--input', '/input.pdf', '--result', '/original', '--output-dir', '/transaction/R1',
        '--mode', mode, '--resume']


def probe():
    facts = {'unexpected_network_interfaces': [name for _, name in socket.if_nameindex() if name != 'lo'],
        'read_only': {path: bool(os.statvfs(path).f_flag & os.ST_RDONLY)
                      for path in ('/product', '/input.pdf', '/original')},
        'GPU_devices': [str(p) for p in Path('/dev').glob('nvidia*')] +
                       ([str(Path('/dev/dxg'))] if Path('/dev/dxg').exists() else []),
        'new_pid_namespace': os.readlink('/proc/self/ns/pid') != os.environ['PDF2MD_FIDELITY_PARENT_PID_NS']}
    if facts['unexpected_network_interfaces'] or facts['GPU_devices'] or not all(facts['read_only'].values()) or not facts['new_pid_namespace']:
        raise ValueError('Fidelity source-only namespace checks failed')
    return facts
