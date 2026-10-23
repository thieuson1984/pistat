"""Make `async def test_*` run under plain pytest.

The suite has to work on a fresh machine with nothing but `pytest` installed —
a dashboard whose tests need a plugin to even be collected is a trap for the
next person (and it silently reported "8 passed" while three suites never ran).

No fixtures are involved, so a bare `asyncio.run()` wrapper is enough.
"""
import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    fn = pyfuncitem.obj
    if not inspect.iscoroutinefunction(fn):
        return None            # let pytest handle the sync tests
    kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(fn(**kwargs))
    return True
