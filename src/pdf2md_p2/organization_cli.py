"""CLI adapters for sealed-document organization; no parser runtime selection."""

import argparse
from copy import deepcopy
from pathlib import Path
import re
import sys

from .common import read


COMMANDS = frozenset(('organize', 'scopes', 'search', 'source-visibility', 'revise', 'export', 'verify-reading'))
DEPENDENCIES = {'PIL': 'Pillow', 'markdown_it': 'markdown-it-py', 'pymupdf': 'PyMuPDF'}


def is_organization_command(arguments):
    """Identify existing-result commands without importing runtime dependencies."""
    return bool(arguments and (arguments[0] in COMMANDS or (
        arguments[0] in ('trace', 'verify') and any(
            item == '--workspace' or item.startswith('--workspace=') for item in arguments[1:]))))


def runtime_guidance():
    return (
        'Use an existing Pdf2Md coordinator environment with the locked runtime dependencies.\n'
        'Linux: /path/to/coordinator/bin/python scripts/pdf2md.py <same arguments>\n'
        'Windows/WSL: .\\scripts\\pdf2md.cmd <same arguments> '
        '--wsl-python /path/to/coordinator/bin/python\n'
        'Keep the existing configuration precedence; correct a conflicting or missing '
        'coordinator_python in the selected configuration.\n'
        'Document organization needs its Python dependencies, not a GPU, model download or parser run. '
        'See docs/CONFIGURATION.md for the coordinator environment.'
    )


def dependency_error(error):
    module = (error.name or '').split('.')[0]
    package = DEPENDENCIES.get(module)
    if package is None:
        return None
    return (f'Document organization runtime dependency unavailable: {package} '
            f'(Python module {error.name}).\nSelected Python: {sys.executable}\n' + runtime_guidance())


def _pages(value):
    if not re.fullmatch(r'[1-9]\d*(?::[1-9]\d*)?', value):
        raise argparse.ArgumentTypeError('Use a physical page number or inclusive START:END range')
    values = [int(part) for part in value.split(':')]
    first, last = values[0], values[-1]
    if first > last:
        raise argparse.ArgumentTypeError('Page range start must not exceed end')
    return first, last


def _nonempty(value):
    if not value.strip():
        raise argparse.ArgumentTypeError('Value must not be blank')
    return value


def _workspace(parser, version=False):
    parser.add_argument('--workspace', type=Path, required=True, help='Independent organized workspace')
    if version:
        parser.add_argument('--revision', help='Read an immutable revision (default: current HEAD)')


def register(sub):
    parser = sub.add_parser('organize', help='Organize a completed conversion without running a parser')
    parser.add_argument('--from-result', type=Path, required=True, help='Read-only sealed conversion result')
    _workspace(parser)
    parser.add_argument('--input', type=Path, help='Optional hash-bound original PDF')
    parser.add_argument('--soft-chars', type=int, default=12000, help='Soft file size target; whole objects take priority')

    parser = sub.add_parser('scopes', help='List explicit, inferred and virtual document scopes')
    _workspace(parser, version=True)

    parser = sub.add_parser('search', help='Filter scope/pages/type before searching complete Units')
    _workspace(parser, version=True)
    parser.add_argument('--query', type=_nonempty)
    parser.add_argument('--match', choices=('exact', 'substring', 'loose'), default='exact')
    parser.add_argument('--scope', help='Scope ID, including its descendants')
    parser.add_argument('--pages', type=_pages, help='Physical page number or inclusive START:END; intersected with scope')
    parser.add_argument('--kind', help='Unit type, e.g. table, formula, paragraph')
    parser.add_argument('--unit-id', help='Stable Unit ID; other filters still apply')
    parser.add_argument('--page-mode', choices=('contained', 'intersect'), default='contained',
                        help='Default excludes cross-boundary objects; intersect explicitly allows them')
    parser.add_argument('--expand-context', action='store_true', help='Include linked context, marking out-of-range objects')

    parser = sub.add_parser('source-visibility', help='Report source text-block associations, not semantic coverage')
    _workspace(parser, version=True)
    parser.add_argument('--input', type=Path, required=True, help='Exact hash-bound original PDF')

    parser = sub.add_parser('revise', help='Propose, check, publish and roll back local revisions')
    actions = parser.add_subparsers(dest='revision_action', required=True)
    proposal = actions.add_parser('propose', help='Store a candidate without publishing it')
    _workspace(proposal)
    proposal.add_argument('--unit-id', required=True)
    proposal.add_argument('--operation', choices=('replace', 'move', 'annotate'), required=True)
    proposal.add_argument('--text-file', type=Path, help='UTF-8 replacement or separate annotation text')
    proposal.add_argument('--target-scope', help='Destination Scope ID for move')
    proposal.add_argument('--reason', type=_nonempty, required=True)
    proposal.add_argument('--proposer', type=_nonempty, required=True)
    proposal.add_argument('--judgment-method', type=_nonempty, default='SOURCE_REVIEW_NOT_HUMAN_VERIFICATION')
    proposal.add_argument('--source-evidence', type=Path, help='JSON array of retained source-region references')
    for action in ('check', 'apply'):
        child = actions.add_parser(action, help='Check candidate' if action == 'check' else 'Explicitly publish a checked candidate')
        _workspace(child)
        child.add_argument('--proposal', type=Path, required=True)
        if action == 'apply':
            child.add_argument('--expected-revision', required=True)
    child = actions.add_parser('history', help='Read revision events and retained failures')
    _workspace(child)
    child = actions.add_parser('rollback', help='Publish a new event restoring a prior content state')
    _workspace(child)
    child.add_argument('--to-revision', required=True)
    child.add_argument('--expected-revision', required=True)
    child.add_argument('--reason', type=_nonempty, required=True)

    child = actions.add_parser('repackage', help='Publish a verified reading export as a new structural revision')
    _workspace(child)
    child.add_argument('--reading-export', type=Path, required=True)
    child.add_argument('--expected-revision', required=True)
    child.add_argument('--reason', type=_nonempty, required=True)

    parser = sub.add_parser('export', help='Generate a derived reading, split or single-file export')
    _workspace(parser, version=True)
    parser.add_argument('--format', choices=('split', 'single', 'reading'), default='split')
    parser.add_argument('--output-dir', type=Path, required=True, help='New external export directory')
    parser.add_argument('--input', type=Path, help='Hash-bound PDF for reading-export structure evidence only')
    parser.add_argument('--target-chars', type=int, help='Reading soft target; natural boundaries and whole objects take priority')

    parser = sub.add_parser('verify-reading', help='Verify a derived reading export without changing the source workspace')
    parser.add_argument('--output-dir', type=Path, required=True)


def validate_args(args, parser):
    if args.command in ('trace', 'verify'):
        organized = bool(getattr(args, 'workspace', None))
        if not organized and getattr(args, 'revision', None):
            parser.error('--revision requires --workspace')
        if args.command == 'trace':
            if organized and not args.unit_id:
                parser.error('Workspace trace requires --unit-id, not --object-id or --line')
            if not organized and args.unit_id:
                parser.error('--unit-id requires --workspace; use --object-id or --line for a conversion result')
    if args.command == 'organize' and args.soft_chars < 1:
        parser.error('--soft-chars must be positive')
    if args.command == 'export':
        if args.format != 'reading' and (args.input is not None or args.target_chars is not None):
            parser.error('--input and --target-chars require --format reading')
        if args.target_chars is not None and args.target_chars < 256:
            parser.error('--target-chars must be at least 256')
    if args.command == 'revise' and args.revision_action == 'propose':
        if args.operation == 'move':
            if not args.target_scope or args.text_file:
                parser.error('Move requires --target-scope and does not accept --text-file')
        elif not args.text_file or args.target_scope:
            parser.error('Replace/annotate require --text-file and do not accept --target-scope')


def dispatch(args):
    """Return a JSON-serializable result, or None for legacy command dispatch."""
    organized = bool(getattr(args, 'workspace', None))
    if args.command not in COMMANDS and not (
            organized and args.command in ('trace', 'verify')):
        return None
    try:
        return _dispatch(args)
    except ModuleNotFoundError as error:
        message = dependency_error(error)
        if message:
            raise RuntimeError(message) from error
        raise


def _dispatch(args):
    from . import organization

    if args.command == 'organize':
        return organization.organize(args.from_result, args.workspace, args.input, args.soft_chars)
    if args.command == 'verify':
        return organization.verify_workspace(args.workspace, args.revision)
    if args.command == 'export':
        if args.format == 'reading':
            from .organization_packaging import export_reading
            return export_reading(organization.snapshot(args.workspace, args.revision), args.output_dir,
                                  args.input, args.target_chars)
        return organization.export(args.workspace, args.output_dir, args.format, args.revision)
    if args.command == 'verify-reading':
        from .organization_packaging import verify_reading
        return verify_reading(args.output_dir)
    if args.command == 'revise':
        action = args.revision_action
        if action == 'propose':
            operation = {'unit_id': args.unit_id, 'operation': args.operation}
            if args.text_file:
                operation['after'] = args.text_file.read_bytes().decode('utf-8')
            if args.target_scope:
                operation['target_scope_id'] = args.target_scope
            if args.source_evidence:
                evidence = read(args.source_evidence)
                if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
                    raise ValueError('Source evidence must be a JSON array of source-region objects')
                operation['source_evidence'] = evidence
            return organization.propose(args.workspace, [operation], args.proposer, args.reason, args.judgment_method)
        if action == 'check':
            return organization.check_proposal(args.workspace, args.proposal)
        if action == 'apply':
            return organization.apply_proposal(args.workspace, args.proposal, args.expected_revision)
        if action == 'history':
            return organization.history(args.workspace)
        if action == 'rollback':
            return organization.rollback(args.workspace, args.to_revision, args.expected_revision, args.reason)
        if action == 'repackage':
            from .organization_packaging import repackage
            return repackage(args.workspace, args.reading_export, args.expected_revision, args.reason)
    snapshot = organization.snapshot(args.workspace, args.revision)
    if args.command == 'scopes':
        return {'workspace_id': snapshot['workspace_id'], 'document_id': snapshot['document_id'],
                'revision': snapshot['revision'], 'scope_count': len(snapshot['scopes']),
                'scopes': deepcopy(snapshot['scopes'])}
    from . import organization_query

    if args.command == 'search':
        return organization_query.search(snapshot, args.query, args.match, args.scope, args.pages,
                                         args.kind, args.unit_id, args.page_mode, args.expand_context)
    if args.command == 'trace':
        return organization_query.trace(snapshot, args.unit_id, args.input, args.render_dir)
    if args.command == 'source-visibility':
        result = organization_query.source_visibility(args.input, snapshot['source_map'])
        result.update(workspace_id=snapshot['workspace_id'], document_id=snapshot['document_id'],
                      revision=snapshot['revision'])
        return result
    raise ValueError('Unrecognized organization command')
