"""
Analysis of async programming errors for PEP-789
================================================

https://peps.python.org/pep-0789/#how-widespread-is-this-bug notes:

    We don't have solid numbers here, but believe that many projects
    are affected in the wild. Since hitting a moderate and a critical
    bug attributed to suspending a cancel scope in the same week at
    work, we've used static analysis with some success.
    Three people Zac spoke to at PyCon recognized the symptoms and
    concluded that they had likely been affected.

This script aims to get some additional quantitative data.
Specificially, we'll:

1. Get each of the most-downloaded 8,000 PyPI packages with either
   ``aio`` or ``async`` in the package name.

2. Run ``flake8-async`` over the source distributions, counting the
   yield-in-cancel-scope errors per-package, per-kloc, and per-yield.

3. Audit a random sample of hits to contextualize whatever numbers
   we find - I expect latent bugs, but if there are many false alarms
   it will be important to reduce the estimated impact accordingly.

"""

import ast
import json
import tarfile
from collections import defaultdict
from contextlib import suppress
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

import httpx
import trio
from flake8_async import Options, Plugin

_client = httpx.AsyncClient(follow_redirects=True)
HERE = trio.Path(__file__).parent / "cache"

sdist_paths: set[trio.Path] = set()


def filter_packages(it: Iterable[str]) -> list[str]:
    includes = ("aio", "async", "anyio", "trio")
    # includes = ("anyio", "trio")
    return sorted(
        name
        for name in (x.lower() for x in it)
        if any(x in name for x in includes) and not name.startswith("types-")
    )


async def get_packagelist_8k():
    p = HERE / "packagelist_8k.json"
    with suppress(FileNotFoundError):
        return json.loads(await p.read_text())
    # Thanks Hugo for https://hugovk.github.io/top-pypi-packages/
    url = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.json"
    response = (await _client.get(url)).raise_for_status().json()
    async_pkgs = filter_packages(p["project"] for p in response["rows"])
    await p.parent.mkdir(parents=True, exist_ok=True)
    await p.write_text(json.dumps(async_pkgs, indent=4))
    return async_pkgs


async def get_packagelist_all():
    HERE / "packagelist_all.json"
    # with suppress(FileNotFoundError):
    #     return json.loads(await p.read_text())
    url = "https://pypi.org/simple/"
    response = (await _client.get(url)).raise_for_status().content
    names = (
        line.split('">')[1].removesuffix("</a>")
        for line in response.decode().splitlines()
        if line.startswith('<a href="/simple/')
    )
    async_pkgs = filter_packages(names)
    # await p.parent.mkdir(parents=True, exist_ok=True)
    # await p.write_text(json.dumps(async_pkgs, indent=4))
    return async_pkgs


limiter = trio.CapacityLimiter(50)


async def get_latest_sdist(pkgname) -> trio.Path:
    # Get the URL for the latest sdist for this package
    async with limiter:
        response = await _client.get(f"https://pypi.org/pypi/{pkgname}/json/")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            print(f"{pkgname} raised {err!r}")
            return
        sdists = [
            [
                [r["upload_time_iso_8601"], r["url"]]
                for r in release
                if r["packagetype"] == "sdist" and not r["yanked"]
                # TODO: we can add an "as-of" condition to ensure consistent results
                #       if we re-run the analysis later; but get it working first.
            ]
            for release in response.raise_for_status().json()["releases"].values()
        ]
        try:
            _, latest = max(sum(sdists, []))
        except ValueError:
            print(f"no sdist for {pkgname}")
            return

        fname = HERE / trio.Path(urlparse(latest).path).name
        if not await fname.is_file():
            await fname.write_bytes(
                (await _client.get(latest)).raise_for_status().content
            )
        sdist_paths.add(fname)
        return fname


def files_from_archive(path: trio.Path) -> dict[str, str]:
    """Return a mapping of fname: source_code from inside an sdist.

    Includes all non-empty .py files _except_ for those under a
    directory containing the strings "test" or "vendor".
    """
    if ".tar" not in path.suffixes:
        return {}
    results = {}
    with tarfile.open(path) as archive:
        for member in archive:
            n = member.name
            if n.endswith(".py") and not (
                "test" in n or "vendor" in n or n.endswith("/setup.py")
            ):
                contents = archive.extractfile(n).read().decode()
                if contents.strip():
                    results[n] = contents
    return results


class AsyncFunctionVisitor(ast.NodeVisitor):
    def __init__(self):
        self.async_functions: List[Dict[str, Any]] = []
        self.function_stack: List[Dict[str, Any]] = []
        self.context_managers = [
            "timeout",
            "TaskGroup",
            "open_nursery",
            "CancelScope",
            "fail_after",
            "move_on_after",
            "fail_at",
            "move_on_at",
        ]

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        function_info = {
            "name": node.name,
            "decorator": self.get_decorator(node),
            "yields": 0,
            "line_start": node.lineno,
            "line_end": self.get_last_line(node),
            "issues": 0,
            "context_managers": defaultdict(int),
        }
        self.function_stack.append(function_info)
        self.generic_visit(node)
        self.async_functions.append(self.function_stack.pop())

    def visit_Yield(self, node: ast.Yield):
        if self.function_stack:
            self.function_stack[-1]["yields"] += 1

    def visit_With(self, node: ast.With):
        if self.function_stack:
            for item in node.items:
                self.check_context_manager(item.context_expr)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith):
        if self.function_stack:
            for item in node.items:
                self.check_context_manager(item.context_expr)
        self.generic_visit(node)

    def check_context_manager(self, node: ast.expr):
        if isinstance(node, ast.Call):
            self.check_context_manager(node.func)
        elif isinstance(node, ast.Attribute):
            cm_name = node.attr
            if cm_name in self.context_managers:
                self.function_stack[-1]["context_managers"][cm_name] += 1
        elif isinstance(node, ast.Name):
            cm_name = node.id
            if cm_name in self.context_managers:
                self.function_stack[-1]["context_managers"][cm_name] += 1

    def get_decorator(self, node: ast.AsyncFunctionDef) -> str:
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                if decorator.id == "asynccontextmanager":
                    return "asynccontextmanager"
            elif isinstance(decorator, ast.Attribute):
                if (
                    decorator.attr == "fixture"
                    and isinstance(decorator.value, ast.Name)
                    and decorator.value.id == "pytest"
                ):
                    return "pytest.fixture"
        return ""

    def get_last_line(self, node: ast.AsyncFunctionDef) -> int:
        return max(
            child.end_lineno for child in ast.walk(node) if hasattr(child, "end_lineno")
        )


def flake8_async_101_errors(source_code: str) -> list[int]:
    plugin = Plugin.from_source(source_code)
    plugin._options = Options(
        enabled_codes={"ASYNC101"},
        autofix_codes=set(),
        error_on_autofix=False,
        no_checkpoint_warning_decorators=(),
        transform_async_generator_decorators=("asyncontextmanager",),
        exception_suppress_context_managers=(),
        startable_in_context_manager=(),
        async200_blocking_calls={},
        anyio=True,
        asyncio=True,
        disable_noqa=True,
    )
    return [err.line for err in plugin.run()]


def analyze_source(source_code: str) -> dict:
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {
            "loc": len(source_code.splitlines()),
            "async_fns": None,
            "async_gen_fns": None,
        }
    visitor = AsyncFunctionVisitor()
    visitor.visit(tree)

    async_fns = len([fn for fn in visitor.async_functions if fn["yields"] == 0])
    async_gen_fns = [fn for fn in visitor.async_functions if fn["yields"] > 0]

    # Only pay the cost of linting if there are async generators in this file
    lint_error_linenos = flake8_async_101_errors(source_code) if async_gen_fns else []

    # Assign errors to the correct functions
    for func in async_gen_fns:
        func["issues"] = sum(
            func["line_start"] <= line <= func["line_end"]
            for line in lint_error_linenos
        )
        func.pop("line_start", None)
        func.pop("line_end", None)

    return {
        "loc": len(source_code.splitlines()),
        "async_fns": async_fns,
        "async_gen_fns": async_gen_fns,
    }


def _analyze_sdist(path: trio.Path) -> dict[str, dict]:
    return {k: analyze_source(v) for k, v in files_from_archive(path).items()}


async def analyze_sdist(path: trio.Path) -> dict[str, dict]:
    # strip dual-suffixes, and add .1 for versioning
    fname = trio.Path(path).with_suffix("").with_suffix(".2.json")
    if not await fname.is_file():
        results = await trio.to_thread.run_sync(_analyze_sdist, path)
        await fname.write_text(json.dumps(results, indent=4))
        return results
    return json.loads(await fname.read_text())


if __name__ == "__main__":

    @trio.run
    async def main():
        pkgs = await get_packagelist_all()
        async with trio.open_nursery() as n:
            for pkg in pkgs:
                n.start_soon(get_latest_sdist, pkg)

        assert len(sdist_paths) <= len(pkgs)  # not all pkgs have an sdist

        async with trio.open_nursery() as n:
            for path in sdist_paths:
                n.start_soon(analyze_sdist, path)

        data = [json.loads(await p.read_text()) for p in await HERE.glob("*.2.json")]
        with_gens = {
            k: d for group in data for k, d in group.items() if d["async_gen_fns"]
        }
        interesting = [
            {"file": k, **fn}
            for k, v in with_gens.items()
            for fn in v["async_gen_fns"]
            if any(
                fn["issues"] or sum(fn["context_managers"].values())
                for fn in v["async_gen_fns"]
            )
        ]
        await trio.Path("interesting.json").write_text(
            json.dumps(interesting, indent=4)
        )
        print(json.dumps([fn for fn in interesting if fn["issues"]], indent=4))
