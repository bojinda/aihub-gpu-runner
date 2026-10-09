import multiprocessing
import tempfile
import unittest
from pathlib import Path
from aihub_gpu_runner.core import NativeLock

def child_attempt(path, output):
    lock = NativeLock(path)
    acquired = lock.acquire(0)
    output.put(acquired)
    lock.close()

class NativeProcessTests(unittest.TestCase):
    def test_os_lock_excludes_other_process_and_keeps_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-resource.lock"
            lock = NativeLock(path)
            self.assertTrue(lock.acquire(0))
            inode = path.stat().st_ino
            context = multiprocessing.get_context("spawn")
            output = context.Queue()
            process = context.Process(target=child_attempt, args=(str(path), output))
            process.start()
            try:
                self.assertFalse(output.get(timeout=5))
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                lock.close()
                output.close()
                output.join_thread()
            self.assertEqual(path.stat().st_ino, inode)
            next_lock = NativeLock(path)
            self.assertTrue(next_lock.acquire(0))
            next_lock.close()

if __name__ == "__main__":
    unittest.main()
