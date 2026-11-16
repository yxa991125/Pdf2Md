"""Bounded arithmetic syntax for source-grounded formula comparison; no evaluation."""

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re


class UnsupportedMath(ValueError):
    pass


@dataclass
class Formula:
    name: str
    expression: str
    tree: object
    tokens: list


class _Latex:
    def __init__(self, text):
        self.text, self.index = text, 0

    def group(self):
        self.space()
        if self.index >= len(self.text) or self.text[self.index] != '{':
            raise UnsupportedMath('Expected a complete braced argument')
        self.index += 1
        result = self.sequence('}')
        if self.index >= len(self.text):
            raise UnsupportedMath('Unclosed argument')
        self.index += 1
        return result

    def space(self):
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def sequence(self, closing=None):
        result = ''
        while self.index < len(self.text):
            char = self.text[self.index]
            if char == closing:
                break
            if char == '{':
                result += '(' + self.group() + ')'
            elif char == '\\':
                match = re.match(r'\\([A-Za-z]+|.)', self.text[self.index:])
                if not match:
                    raise UnsupportedMath('Incomplete command')
                command = match[1]
                self.index += len(match[0])
                if command in ('left', 'right', ',', ';', '!', ' ', 'quad', 'qquad'):
                    continue
                if command in ('mathsf', 'mathrm', 'operatorname', 'text', 'textrm', 'mathbf'):
                    raw = self.group()
                    # Letterspacing inside an explicit upright/style group is visual.
                    result += re.sub(r'\s+', '', raw)
                elif command in ('frac', 'dfrac', 'tfrac'):
                    numerator, denominator = self.group(), self.group()
                    result += '((' + numerator + ')/(' + denominator + '))'
                elif command in ('times', 'cdot'):
                    result += '*'
                elif command == 'div':
                    result += '/'
                else:
                    raise UnsupportedMath('Unsupported or damaged command: ' + command)
            elif char in ('_', '^'):
                self.index += 1
                self.space()
                if self.index < len(self.text) and self.text[self.index] == '{':
                    value = self.group()
                else:
                    token = re.match(r'[A-Za-z0-9]', self.text[self.index:])
                    if not token:
                        raise UnsupportedMath('Missing script argument')
                    value = token[0]
                    self.index += len(value)
                if char == '_':
                    value = re.sub(r'\s+', '', value)
                    if not re.fullmatch(r'[A-Za-z0-9]+', value):
                        raise UnsupportedMath('Nonliteral or nested subscript')
                    result = result.rstrip() + '__' + value
                else:
                    result = result.rstrip() + '**(' + value + ')'
            elif char.isspace():
                self.index += 1
            elif char in '\u2212\u2013\u00d7\u00b7':
                result += {'\u2212': '-', '\u2013': '-', '\u00d7': '*', '\u00b7': '*'}[char]
                self.index += 1
            else:
                result += char
                self.index += 1
        return result


def _tree(node, source):
    if isinstance(node, ast.Expression):
        return _tree(node.body, source)
    if isinstance(node, ast.Name) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', node.id):
        return ('name', node.id)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        try:
            value = Decimal(ast.get_source_segment(source, node))
            if not value.is_finite():
                raise InvalidOperation
            # Decimal equality avoids both 2/2.0 false differences and float
            # rounding that could falsely equate distinct long constants.
            with localcontext() as context:
                context.prec = max(28, len(value.as_tuple().digits))
                return ('number', str(value.normalize()))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise UnsupportedMath('Unsupported numeric literal') from error
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        return (type(node.op).__name__, _tree(node.left, source), _tree(node.right, source))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return (type(node.op).__name__, _tree(node.operand, source))
    raise UnsupportedMath('Unsupported arithmetic structure')


def parse(text):
    if len(text) > 12000:
        raise UnsupportedMath('Expression exceeds bounded parser size')
    expression = _Latex(text).sequence()
    if expression.count('=') != 1:
        raise UnsupportedMath('One assignment required')
    name, rhs = expression.split('=')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
        raise UnsupportedMath('Nonliteral formula name')
    try:
        tree = _tree(ast.parse(rhs, mode='eval'), rhs)
    except (SyntaxError, RecursionError, ValueError) as error:
        raise UnsupportedMath(str(error)) from error
    tokens = re.findall(r'\*\*|[A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]', expression)
    return Formula(name, expression, tree, tokens)


def formula_name(text):
    """Read only the LHS when the RHS itself is damaged; never guess by RHS values."""
    if '=' not in text:
        return None
    try:
        lhs = _Latex(text.split('=', 1)[0]).sequence()
    except UnsupportedMath:
        return None
    return lhs if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', lhs) else None


def differences(source, actual):
    expected = parse(source)
    try:
        observed = parse(actual)
    except UnsupportedMath as error:
        # Unknown notation is not itself evidence of a semantic error. A literal
        # underscore replacing a demonstrably absent subtraction is a narrower case.
        def subtractions(tree):
            return int(tree[0] == 'Sub') + sum(subtractions(t) for t in tree[1:] if isinstance(t, tuple))
        missing = subtractions(expected.tree) > 0 and not re.search(r'[-\u2212\u2013]', actual)
        demonstrated = missing and r'\_' in actual
        return {'status': 'SOURCE_MISMATCH' if demonstrated else 'NOT_CHECKED',
                'differences': ['SUBTRACTION_OPERATOR_ABSENT', 'UNDERSCORE_SCRIPT_INSERTED'] if demonstrated else [],
                'expected_tree': expected.tree, 'actual_tree': None, 'detail': str(error)}
    if expected.name != observed.name:
        return {'status': 'ALIGNMENT_AMBIGUOUS', 'differences': ['DIFFERENT_FORMULA_NAME']}
    return {'status': 'SOURCE_MATCH' if expected.tree == observed.tree else 'SOURCE_MISMATCH',
            'differences': [] if expected.tree == observed.tree else ['OPERANDS_OPERATORS_OR_SCRIPT_STRUCTURE'],
            'expected_tree': expected.tree, 'actual_tree': observed.tree}
