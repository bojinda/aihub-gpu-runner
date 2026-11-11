"""Run canonical tests against an already installed runner, never a checkout."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

def cases(value):
    if isinstance(value, unittest.TestSuite):
        for child in value:
            yield from cases(child)
    else:
        yield value

def main():
    # Resolve before making test helpers visible. Spawned child interpreters
    # inherit site-packages before the test checkout, too.
    import aihub_gpu_runner
    package = Path(aihub_gpu_runner.__file__).resolve().parent
    if package.is_relative_to(ROOT):
        raise RuntimeError("installed_runner_required_checkout_import_refused")
    sys.path.append(str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    sys.path[:] = [p for p in sys.path if Path(p or '.').resolve() != ROOT] + [str(ROOT)]
    definitions = set()
    for case in cases(suite):
        method = getattr(case, case._testMethodName)
        definitions.add((inspect.getsourcefile(method), inspect.getsourcelines(method)[1], method.__qualname__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    origins = {}
    for name, module in list(sys.modules.items()):
        if name == "aihub_gpu_runner" or name.startswith("aihub_gpu_runner."):
            path = Path(module.__file__).resolve()
            if not path.is_relative_to(package):
                raise RuntimeError("runner_import_origin_changed:" + name)
            origins[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    directory = Path(os.environ["AIHUB_QUALIFICATION_RESULTS"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "installed-suite-result.json").write_text(json.dumps({
        "python": sys.version, "executions": result.testsRun,
        "unique_method_definitions": len(definitions), "failures": len(result.failures),
        "errors": len(result.errors), "skips": len(result.skipped),
        "initialization_errors": unittest.defaultTestLoader.errors, "origins": origins,
        "successful": result.wasSuccessful(),
    }, indent=2))
    return not result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
