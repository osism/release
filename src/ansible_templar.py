# Variable templating as ansible-core 2.18 does it, for resolving the image
# references of kolla-ansible (src/kolla_tags.py).
#
# ansible-core 2.18 renders templates with jinja2_native off, its default,
# which nothing in OSISM changes: a template renders to text, and only text
# that looks like a dict, a list or True/False is converted back
# (ansible_eval_concat in lib/ansible/template/native_helpers.py). A string
# without template markers is not templated at all. So a tag like "2026.10"
# keeps its text, and "{{ false }}" used as a condition elsewhere is a
# boolean. ansible-core 2.19 changes these rules; the caller refuses other
# versions (SUPPORTED_ANSIBLE_CORE).
#
# 2.20 is accepted as well because it was measured to resolve the image
# references identically: over the OSISM 11.0.0 inputs
# (tests/fixtures/kolla-tags-11.0.0.json.gz, 153 *_image_full in 71 roles)
# this model, the Templar of ansible-core 2.18.18 and that of 2.20.10 agree
# on every reference. Where 2.19+ differs, a template that makes up a whole
# value and renders a non-string, no image reference goes. kolla-ansible
# 2026.1 requires ansible-core >=2.19, so its image runs 2.20.
#
# Variables come from layers with a rank: a higher rank wins, and layers of
# the same rank have to agree on a value, compared after resolving it in
# each. Jinja2 looks up every name of a template before it evaluates it,
# also the names of a branch it does not take. So a variable whose
# resolution fails (a recursive loop, a rendering error, a disagreement)
# becomes a Failed value, which raises only when it is used.

import ast
from dataclasses import dataclass
from itertools import chain, islice
from types import GeneratorType

import jinja2
from jinja2.runtime import Context
from jinja2.utils import missing

SUPPORTED_ANSIBLE_CORE = ("2.18.", "2.20.")
TEMPLATE_MARKERS = ("{{", "{%", "{#")
_JSON_NAMES = {"true": True, "false": False, "null": None}


class TemplateFailure(Exception):
    """A variable that cannot be resolved."""


@dataclass(frozen=True)
class Hop:
    """A variable resolved on the way: its name, raw value and layer."""

    name: str
    raw: object
    layer: str


class Failed:
    """The value of a variable whose resolution failed.

    Any use raises the failure, so a failure in a branch that is never used
    does not fail the expression. It is not a jinja2 Undefined, so default()
    passes it through instead of replacing it, as an Ansible templating error
    would not be replaced either.
    """

    def __init__(self, failure):
        object.__setattr__(self, "failure", failure)

    def _raise(self, *args, **kwargs):
        raise self.failure

    def __getattr__(self, name):
        raise self.failure

    __str__ = __bool__ = __len__ = __iter__ = __contains__ = _raise
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _raise
    __add__ = __radd__ = __mul__ = __rmul__ = __mod__ = __rmod__ = _raise
    __getitem__ = __int__ = __float__ = _raise

    def __repr__(self):
        return f"Failed({self.failure})"


class _Json2Python(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id not in _JSON_NAMES:
            return node
        return ast.Constant(_JSON_NAMES[node.id])


def ansible_concat(nodes):
    """ansible-core 2.18's ansible_eval_concat, without its unsafe tracking."""
    head = list(islice(nodes, 2))
    if not head:
        return ""
    if len(head) == 1:
        out = str(head[0])
    else:
        if isinstance(nodes, GeneratorType):
            nodes = chain(head, nodes)
        out = "".join(str(value) for value in nodes)
    if out.startswith(("{", "[")) or out in ("True", "False"):
        try:
            out = ast.literal_eval(
                ast.fix_missing_locations(
                    _Json2Python().visit(ast.parse(out, mode="eval"))
                )
            )
        except (TypeError, ValueError, SyntaxError, MemoryError):
            pass
    return out


class _Environment(jinja2.Environment):
    concat = staticmethod(ansible_concat)


class Templar:
    """Resolves variables over ranked layers, as Ansible would."""

    def __init__(self, layers):
        """layers: [(rank, {name: (raw value, layer label)})]."""
        self.layers = layers
        self.memo = {}  # name -> (value, hops)
        self.stack = []  # names being resolved
        self.tainted = {}  # name -> lowest stack index of an unfinished name it read
        self.hops = []
        templar = self

        class _Context(Context):
            def resolve_or_missing(self, key):
                value = templar._lookup(key)
                if value is missing:
                    return super().resolve_or_missing(key)
                return value

        self.environment = _Environment(undefined=jinja2.StrictUndefined)
        self.environment.context_class = _Context

    def resolve(self, name):
        """The value of name and the hops that led to it.

        Raises TemplateFailure if name is not defined or cannot be resolved.
        """
        self.hops = []
        value = self._lookup(name)
        if value is missing:
            raise TemplateFailure(f"{name} is not defined")
        if isinstance(value, Failed):
            raise TemplateFailure(f"{name}: {value.failure}")
        return value, list(self.hops)

    def _candidates(self, name):
        """The (raw, label) entries of the highest rank defining name."""
        found = [(rank, values[name]) for rank, values in self.layers if name in values]
        if not found:
            return []
        top = max(rank for rank, _ in found)
        return [entry for rank, entry in found if rank == top]

    def _lookup(self, name):
        if name in self.memo:
            value, hops = self.memo[name]
            self.hops.extend(hops)
            return value
        candidates = self._candidates(name)
        if not candidates:
            return missing
        if name in self.stack:
            index = self.stack.index(name)
            for unfinished in self.stack[index + 1 :]:
                self.tainted[unfinished] = min(
                    self.tainted.get(unfinished, index), index
                )
            loop = " -> ".join([*self.stack[index:], name])
            return Failed(TemplateFailure(f"recursive loop: {loop}"))
        depth = len(self.stack)
        outer, self.hops = self.hops, []
        self.stack.append(name)
        try:
            values = [self._template(raw) for raw, _ in candidates]
        finally:
            self.stack.pop()
        value = self._agree(name, candidates, values)
        layer = " = ".join(label for _, label in candidates)
        hops = [Hop(name, candidates[0][0], layer), *self.hops]
        self.hops = outer + hops
        # A value that read an unfinished ancestor holds a stand-in for it and
        # is only valid inside this resolution: keep it out of the memo.
        if self.tainted.pop(name, depth) >= depth:
            self.memo[name] = (value, hops)
        return value

    def _agree(self, name, candidates, values):
        if len(values) == 1:
            return values[0]
        for value in values:
            if isinstance(value, Failed):
                return value
        try:
            agree = all(value == values[0] for value in values[1:])
        except TemplateFailure as e:  # a container holding a failed value
            return Failed(e)
        if agree:
            return values[0]
        described = ", ".join(
            f"{label}: {value!r}" for (_, label), value in zip(candidates, values)
        )
        return Failed(
            TemplateFailure(
                f"{name} differs between layers of the same rank ({described})"
            )
        )

    def _template(self, raw):
        if isinstance(raw, str):
            if not any(marker in raw for marker in TEMPLATE_MARKERS):
                return raw
            try:
                return self.environment.from_string(raw).render()
            except TemplateFailure as e:
                return Failed(e)
            except Exception as e:  # any rendering error makes a failed value
                return Failed(TemplateFailure(f"{type(e).__name__}: {e}"))
        if isinstance(raw, list):
            return [self._template(item) for item in raw]
        if isinstance(raw, dict):
            return {key: self._template(item) for key, item in raw.items()}
        return raw
