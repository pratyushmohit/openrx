import ast
import inspect

from openrx.catalog.functions import catalog_functions
from openrx.catalog.loader import load_catalog
from openrx.runner.stubs import stubs_for

FUNCTIONS = catalog_functions(load_catalog())


def stub_functions(text):
    """name -> ast function node, for every function the stub defines."""
    tree = ast.parse(text)
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def stub_params(node):
    """[(name, default as source text or None)] of a stub function."""
    args = node.args.args
    defaults = [None] * (len(args) - len(node.args.defaults)) + [
        ast.unparse(d) for d in node.args.defaults
    ]
    return [(a.arg, d) for a, d in zip(args, defaults, strict=True)]


def real_params(fn):
    return [
        (p.name, None if p.default is inspect.Parameter.empty else repr(p.default))
        for p in inspect.signature(fn).parameters.values()
    ]


def test_stubs_are_valid_python():
    for runner in ("search", "execute"):
        compile(stubs_for(runner), f"<{runner} stubs>", "exec")


def test_catalog_stubs_match_the_real_functions():
    # Names, order and defaults must agree, or the type checker would accept calls
    # that fail at run time (or reject calls that work).
    stubs = stub_functions(stubs_for("search"))

    assert set(stubs) == set(FUNCTIONS)
    for name, fn in FUNCTIONS.items():
        assert stub_params(stubs[name]) == real_params(fn), name


def test_search_code_cannot_see_request():
    assert "request" not in stub_functions(stubs_for("search"))


def test_execute_stubs_add_an_async_request():
    stubs = stub_functions(stubs_for("execute"))

    assert set(stubs) == set(FUNCTIONS) | {"request"}
    assert isinstance(stubs["request"], ast.AsyncFunctionDef)
    assert stub_params(stubs["request"]) == [("source", None), ("path", None), ("params", "None")]


def test_response_type_lists_the_fields_model_code_gets():
    tree = ast.parse(stubs_for("execute"))
    [response] = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Response"]

    fields = [n.target.id for n in response.body if isinstance(n, ast.AnnAssign)]  # type: ignore[union-attr]
    assert fields == ["status", "data", "truncated", "notes"]
