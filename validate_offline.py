"""Offline test runner: JSON evidence, no backend or deployment activity."""
import hashlib
import io
import json
import platform
from pathlib import Path
import sys
import unittest

class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subcases = 0
        self.names = []
    def addSuccess(self, test):
        self.names.append(test.id())
        super().addSuccess(test)
    def addSubTest(self, test, subtest, err):
        self.subcases += 1
        super().addSubTest(test, subtest, err)

def main():
    root = Path(__file__).resolve().parent
    output = Path(sys.argv[1]).resolve()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    files = {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*.py") if "__pycache__" not in p.parts}
    report = {"checkpoint": "R1 offline", "tests": result.testsRun, "subcases": result.subcases,
              "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.wasSuccessful(), "platform": platform.system(),
              "native_lock_backend": "Windows byte locking" if sys.platform == "win32" else "POSIX flock",
              "test_names_passed": result.names, "source_sha256": files,
              "live_gpu_work": False, "deployment": False, "callers_resumed": False,
              "meeting_pipeline_modified": False,
              "limits": ["Linux deployment/durability not established by Windows execution",
                        "ComfyUI memory cleanup calibration and installed-version behavior require separate live approval",
                        "Legacy meeting/application admission integration not implemented; no global coordination claim"],
              "output": stream.getvalue()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(stream.getvalue())
    print(json.dumps({k: report[k] for k in ("tests", "subcases", "failures", "errors", "passed")}))
    raise SystemExit(0 if report["passed"] else 1)

if __name__ == "__main__":
    main()
