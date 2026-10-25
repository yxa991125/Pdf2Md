"""Select once before execution; resume never changes an established backend."""
from pathlib import Path
import json
import subprocess

from .configuration import CPU_POLICY, POLICY, resolve_device, validate


class NoBackendAvailable(ValueError):
    def __init__(self, reports):
        self.reports = reports
        super().__init__('No prepared backend is available. Check the one-time setup in '
                         'docs/CONFIGURATION.md (Chinese: docs/CONFIGURATION.zh-CN.md), or select an '
                         'existing deployment with --config /path/runtime.json or --wsl-python /path/bin/python. '
                         'Backend checks: ' + json.dumps(reports))


def candidate(config, device):
    policy = CPU_POLICY if device == 'cpu' else POLICY
    result = {**config, **policy, 'resolved_compute': device}
    result.setdefault('compute', 'auto')
    result.setdefault('cpu_python', config['coordinator_python'])
    if device == 'cpu':
        result.update(authorized_gpu='0', device_files=[])
    return validate(result)


def select(config, pinned=None, probe_all=False):
    from .environment import inspect
    requested = config.get('compute', 'auto')
    selected = pinned.get('resolved_compute') if pinned else config.get('resolved_compute')
    if selected and requested != 'auto' and selected != requested:
        raise ValueError('Requested compute mode conflicts with the pinned backend')
    reports, candidates = {}, {}
    modes = [selected] if selected else (['cuda', 'cpu'] if requested == 'auto' else [requested])
    probes = list(dict.fromkeys([*modes, *(['cuda', 'cpu'] if probe_all else [])]))
    for mode in probes:
        value = candidate(config, mode)
        try:
            if mode == 'cuda':
                value = resolve_device(value)
            report = inspect(value)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            # Invalid inventories/explicit authorization are not hardware absence.
            if isinstance(error, ValueError) and not any(s in str(error) for s in (
                    'Cannot detect', 'detection failed', 'No NVIDIA GPU found', 'CUDA_VISIBLE_DEVICES does not authorize')):
                raise
            report = {'ready_for_conversion': False, 'error': str(error), 'checks': []}
        reports[mode] = report
        if mode in modes and not candidates and any(row.get('integrity_violation') for row in report.get('checks', [])):
            raise ValueError('Backend binding integrity failure: ' + str(report['checks']))
        if report['ready_for_conversion']:
            candidates[mode] = value
    chosen = next((mode for mode in modes if mode in candidates), None)
    if chosen is None:
        raise NoBackendAvailable(reports)
    result = candidates[chosen]
    result['selection_reason'] = (pinned.get('selection_reason') if pinned else None) or (
        'PINNED_' + chosen.upper() if selected else 'EXPLICIT_' + chosen.upper() if requested != 'auto'
        else 'AUTO_CUDA_AVAILABLE' if chosen == 'cuda' else 'AUTO_CPU_GPU_UNAVAILABLE')
    return result, reports
