"""Black-box deploy-helper regressions; only temporary files and stub tools run.

Set AIHUB_DEPLOY_HELPER_UNDER_TEST to an older helper to reproduce a regression.
No installed runner, Docker daemon, system service, network, or GPU is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv


BRANCH = "codex/r1-gpu-runner"
HELPER = Path(os.environ.get("AIHUB_DEPLOY_HELPER_UNDER_TEST",
                            Path(__file__).resolve().parents[1] / "deploy.sh"))
PACKAGE = {
    "__init__.py": '"""Installed fixture package."""\n',
    "config.py": (
        "import json\nfrom pathlib import Path\n"
        "def load_config(path):\n"
        "    value = json.loads(path.read_text())\n"
        "    return value, {}, Path(value['state_dir']), {}, 'fixture-scope'\n"
    ),
    "core.py": (
        "import json\nFINAL = {'success', 'failed', 'cancelled'}\n"
        "def strict_json(data):\n    return json.loads(data)\n"
    ),
}
STUB = """#!{python}
import json, os, sys
from pathlib import Path
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
read_only = ((tool == 'systemctl' and args == ['is-active', '--quiet', 'aihub-gpu-runner.service'])
             or (tool == 'docker' and args[:2] == ['inspect', '-f']
                 and args[-1:] == ['comfyui-wallpaper']))
with open(os.environ['AIHUB_TEST_CALL_LOG'], 'a') as stream:
    stream.write(json.dumps(dict(tool=tool, args=args, mutation=not read_only)) + '\\n')
if not read_only:
    sys.exit(91)
if tool == 'docker':
    print('false|0|no' if 'RestartPolicy' in args[2] else 'false|0')
"""
PIP_STUB = """import json, os, sys
with open(os.environ['AIHUB_TEST_CALL_LOG'], 'a') as stream:
    stream.write(json.dumps(dict(tool='pip', args=sys.argv[1:], mutation=True)) + '\\n')
raise SystemExit(92)
"""


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("git")
                     and shutil.which("bash"), "requires Linux, git, and bash")
class DeployHelperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runner-helper-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        self.source = self.repo / "aihub_gpu_runner"
        self.source.mkdir()
        for name, content in PACKAGE.items():
            (self.source / name).write_text(content)
        (self.repo / "deploy.sh").write_bytes(HELPER.read_bytes())
        self.venv = self.root / "venv"
        venv.EnvBuilder(with_pip=False).create(self.venv)
        self.python = self.venv / "bin" / "python"
        site = subprocess.check_output([
            str(self.python), "-I", "-B", "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ], text=True).strip()
        self.site = Path(site)
        self.installed = self.site / "aihub_gpu_runner"
        shutil.copytree(self.source, self.installed)
        pip = self.site / "pip"
        pip.mkdir()
        (pip / "__init__.py").write_text("")
        (pip / "__main__.py").write_text(PIP_STUB)
        self.state = self.root / "state"
        (self.state / "jobs").mkdir(parents=True)
        (self.state / "jobs" / "completed.json").write_text('{"state":"success"}')
        self.ownership = {
            "scope": "fixture-scope", "ready": True, "owners": {},
            "verified_boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "boot_recovery_status": "ready",
        }
        self.write_ownership()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"state_dir": str(self.state)}))
        self.calls = self.root / "calls.jsonl"
        stubs = self.root / "stubs"
        stubs.mkdir()
        for name in ("systemctl", "docker", "sudo"):
            path = stubs / name
            path.write_text(STUB.format(python=sys.executable))
            path.chmod(0o755)
        self.env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONPYCACHEPREFIX", "GIT_DIR",
                    "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(key, None)
        self.env.update({
            "PATH": str(stubs) + os.pathsep + self.env.get("PATH", ""),
            "AIHUB_RUNNER_VENV": str(self.venv),
            "AIHUB_RUNNER_CONFIG": str(self.config),
            "AIHUB_TEST_CALL_LOG": str(self.calls),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        })
        self.git("init", "-q", "-b", BRANCH)
        self.git("config", "user.name", "Helper Test")
        self.git("config", "user.email", "helper-test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        # A local tracking ref is sufficient: the helper never fetches or pushes.
        self.git("remote", "add", "origin", str(self.root / "unused-local-remote"))
        self.commit()
        self.git("branch", "--set-upstream-to", "origin/" + BRANCH)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              check=True, capture_output=True, text=True)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "Fixture checkpoint")
        self.git("update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")

    def write_ownership(self):
        (self.state / "ownership.json").write_text(json.dumps(self.ownership))

    def run_helper(self, *args, cwd=None):
        result = subprocess.run(["bash", str(self.repo / "deploy.sh"), *args],
                                cwd=cwd or self.repo, env=self.env,
                                input="DEPLOY RUNNER\n", capture_output=True,
                                text=True, timeout=20)
        events = ([json.loads(line) for line in self.calls.read_text().splitlines()]
                  if self.calls.exists() else [])
        self.assertFalse([event for event in events if event["mutation"]],
                         result.stdout + result.stderr + repr(events))
        return result

    def assert_refused(self, *args):
        result = self.run_helper(*args)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Before proceeding:", result.stdout)
        self.assertNotIn("differs from the installed package", result.stdout)
        return result

    def assert_different(self, *args, cwd=None):
        result = self.run_helper(*args, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("differs from the installed package", result.stdout)
        self.assertIn("CHECK ONLY", result.stdout)
        return result

    def test_matching_installed_source_default_check(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("installed venv package Python source matches Git", result.stdout)
        self.assertIn(str(self.installed / "__init__.py"), result.stdout)

    def test_checkout_does_not_hide_installed_mismatch(self):
        with (self.installed / "core.py").open("a") as stream:
            stream.write("\n# Deliberately different installed source.\n")
        self.assert_different("--check")

    def test_poisoned_checkout_cannot_execute(self):
        marker = self.root / "checkout-imported"
        (self.source / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            "raise RuntimeError('CHECKOUT_IMPORT_EXECUTED')\n")
        self.commit()
        self.assert_different()
        self.assertFalse(marker.exists())

    def test_pythonpath_package_cannot_execute(self):
        rogue = self.root / "pythonpath"
        shutil.copytree(self.source, rogue / "aihub_gpu_runner")
        marker = self.root / "pythonpath-imported"
        (rogue / "aihub_gpu_runner" / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            "raise RuntimeError('PYTHONPATH_IMPORT_EXECUTED')\n")
        self.env["PYTHONPATH"] = str(rogue)
        result = self.run_helper("--check", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())

    def test_missing_installed_package_refuses_apply(self):
        shutil.rmtree(self.installed)
        self.assert_refused("--apply")

    def test_foreign_package_via_pth_refuses_apply(self):
        foreign = self.root / "editable-source"
        shutil.copytree(self.source, foreign / "aihub_gpu_runner")
        shutil.rmtree(self.installed)
        (self.site / "editable-runner.pth").write_text(str(foreign) + "\n")
        self.assert_refused("--apply")

    def test_installed_package_symlink_to_checkout_refuses(self):
        shutil.rmtree(self.installed)
        self.installed.symlink_to(self.source, target_is_directory=True)
        self.assert_refused("--apply")

    def test_installed_module_symlink_to_checkout_refuses(self):
        module = self.installed / "config.py"
        module.unlink()
        module.symlink_to(self.source / "config.py")
        self.assert_refused("--apply")

    def test_source_package_root_symlink_refuses(self):
        shutil.rmtree(self.source)
        self.source.symlink_to(self.installed, target_is_directory=True)
        self.commit()
        self.assert_refused("--apply")

    def test_wrong_interpreter_prefix_refuses(self):
        other = self.root / "other-venv"
        (other / "bin").mkdir(parents=True)
        (other / "bin" / "python").symlink_to(self.python)
        self.env["AIHUB_RUNNER_VENV"] = str(other)
        self.assert_refused("--apply")

    def test_nested_module_difference_is_detected(self):
        for package in (self.source, self.installed):
            (package / "nested").mkdir()
            (package / "nested" / "__init__.py").write_text("")
        (self.source / "nested" / "value.py").write_text("VALUE = 1\n")
        (self.installed / "nested" / "value.py").write_text("VALUE = 2\n")
        self.commit()
        self.assert_different("--check")

    def test_extra_installed_nested_module_is_detected(self):
        (self.installed / "extra").mkdir()
        (self.installed / "extra" / "module.py").write_text("VALUE = 2\n")
        self.assert_different("--check")

    def test_state_failure_refuses_apply(self):
        self.ownership["owners"] = {"gpu0": "active-job"}
        self.write_ownership()
        self.assert_refused("--apply")

    def test_missing_jobs_directory_refuses(self):
        shutil.rmtree(self.state / "jobs")
        self.assert_refused("--check")

    def test_parity_error_cannot_fall_through_to_apply(self):
        # State still passes using installed modules; source parity must error.
        (self.source / "__init__.py").unlink()
        self.commit()
        result = self.assert_refused("--apply")
        self.assertIn("manifest", result.stderr)

    def test_checks_create_no_package_bytecode(self):
        result = self.run_helper("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(list(self.source.rglob("__pycache__")))
        self.assertFalse(list(self.installed.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
