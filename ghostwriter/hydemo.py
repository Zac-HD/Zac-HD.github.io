import ast
import builtins
import importlib
from contextlib import redirect_stdout
from difflib import get_close_matches
from io import StringIO
from traceback import format_exception
from typing import Literal

import black

from hypothesis.extra import ghostwriter


async def get_module_by_name(modulename):
    try:
        return importlib.import_module(modulename)
    except ImportError:
        import micropip

        pkgname, *_ = modulename.split(".")
        await micropip.install(pkgname)
        return importlib.import_module(modulename)


async def get_object_by_name(s: str) -> object:
    """This "type" imports whatever object is named by a dotted string."""
    try:
        return importlib.import_module(s)
    except ImportError:
        pass
    if s in dir(builtins):
        modulename, module, funcname = "builtins", builtins, s
    elif "." not in s:
        return await get_module_by_name(s)
    else:
        modulename, funcname = s.rsplit(".", 1)
        module = await get_module_by_name(modulename)
    try:
        return getattr(module, funcname)
    except AttributeError:
        public_names = [name for name in vars(module) if not name.startswith("_")]
        matches = get_close_matches(funcname, public_names)
        raise AttributeError(
            f"Found the {modulename!r} module, but it doesn't have a "
            f"{funcname!r} attribute."
            + (f"  Closest matches: {matches!r}" if matches else "")
        ) from None


async def write_a_test(
    func: str,
    writer: Literal["magic", "roundtrip", "equivalent"],
    style: Literal["pytest", "unittest"],
):
    try:
        funcs = [await get_object_by_name(name.strip(",")) for name in func.split()]
        if writer in ("roundtrip", "equivalent") and len(funcs) < 2:
            raise RuntimeError(f"{writer} requires multiple functions, got {funcs}")
        return getattr(ghostwriter, writer)(*funcs, style=style)
    except BaseException as err:
        print(format_exception(err))
        return f"# {type(err).__name__}: {err}"


def format_code(source_code):
    msg = "# TODO: replace st.nothing() with an appropriate strategy\n\n"
    if msg in source_code and source_code.count("st.nothing()") == 1:
        source_code = source_code.replace(msg, "")
    try:
        return black.format_str(source_code, mode=black.FileMode())
    except Exception:
        return source_code


PRELUDE = """\
from hypothesis import settings
settings.register_profile("browser", deadline=None, derandomize=True)
settings.load_profile("browser")
"""

# Pytest caches file contents, so increment the filename on each execution.
COUNT = 0


async def run_tests(source_code, *, try_install=True):
    ns = {}
    global COUNT
    COUNT += 1
    fname = f"test_{COUNT}.py"
    try:
        exec(compile(PRELUDE + source_code, fname, "exec"), ns, ns)
    except ModuleNotFoundError:
        if try_install:
            # If we're running tests from cache, run the install-on-import logic
            for n in ast.walk(ast.parse(source_code)):
                if isinstance(n, ast.Import):
                    for alias in n.names:
                        await get_module_by_name(alias.name)
                elif isinstance(n, ast.ImportFrom):
                    await get_module_by_name(n.module)
            return await run_tests(source_code, try_install=False)
        raise

    if "\nimport unittest\n" in source_code and "unittest" in ns:
        unittest = ns["unittest"]
        suite = unittest.TestSuite()
        for cls in ns.values():
            if isinstance(cls, type) and issubclass(cls, unittest.TestCase):
                for name in dir(cls):
                    if name.startswith("test_"):
                        suite.addTest(cls(name))
        buf = StringIO()
        unittest.TextTestRunner(stream=buf).run(suite)
        return buf.getvalue()

    with open("conftest.py", "w") as f:
        f.write(PRELUDE)
    with open(fname, "w") as f:
        f.write(source_code)

    pytest = await get_module_by_name("pytest")
    args = ["-v", "--tb=native", "-W=ignore::pytest.PytestAssertRewriteWarning"]
    with redirect_stdout(buf := StringIO()):
        pytest.main([*args, fname])
    return buf.getvalue()
