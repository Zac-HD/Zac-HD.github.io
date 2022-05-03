import builtins
import importlib
from difflib import get_close_matches
from io import StringIO
from traceback import format_exception
from typing import Literal

from hypothesis.extra import ghostwriter
from hypothesis.reporting import with_reporter


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
        funcs = [await get_object_by_name(name) for name in func.split()]
        if writer in ("roundtrip", "equivalent") and len(funcs) < 2:
            raise RuntimeError(f"{writer} requires multiple functions, got {funcs}")
        return getattr(ghostwriter, writer)(*funcs, style=style)
    except BaseException as err:
        print(format_exception(err))
        return f"# {type(err).__name__}: {err}"


def run_tests(source_code):
    ns = {}
    exec(source_code, ns, ns)

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

    parts = []
    for k, v in list(ns.items()):
        if k.startswith("test"):
            try:
                with with_reporter(parts.append):
                    v()
                parts.append(f"{k}... passed")
            except Exception as err:
                tb = "".join(
                    format_exception(err.with_traceback(err.__traceback__.tb_next))
                ).strip()
                parts.append(f"{k}... failed\n{tb}")
            parts.append("\n")
    return "\n".join(parts)
