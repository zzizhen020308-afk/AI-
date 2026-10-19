import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from novel_loop.cli import main


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = str(Path(self._tmp.name) / "novel")
        # 保证走 mock provider，不受开发机环境影响
        patcher = mock.patch.dict(os.environ, {"NOVEL_API_KEY": ""}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--root", self.root] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_init_then_status(self):
        code, out, _ = self.run_cli("init", "--title", "雨夜收据")
        self.assertEqual(code, 0)
        self.assertIn("已创建小说项目", out)
        self.assertTrue((Path(self.root) / "project.json").is_file())

        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("雨夜收据", out)
        self.assertIn("还没有章节", out)

    def test_init_is_idempotent(self):
        self.run_cli("init", "--title", "A")
        code, out, _ = self.run_cli("init", "--title", "B")
        self.assertEqual(code, 0)
        self.assertIn("项目已存在", out)

    def test_plan_and_write(self):
        self.run_cli("init", "--title", "雨夜收据")
        code, out, _ = self.run_cli("plan", "--chapters", "3")
        self.assertEqual(code, 0)
        self.assertIn("第 1 章", out)

        code, out, _ = self.run_cli("write", "1")
        self.assertEqual(code, 0)
        self.assertIn("第 1 稿", out)
        self.assertIn("编辑自评", out)

        code, out, _ = self.run_cli("review", "1")
        self.assertEqual(code, 0)
        self.assertIn("第 1 章", out)

    def test_write_unknown_chapter_reports_error(self):
        self.run_cli("init", "--title", "A")
        code, _, err = self.run_cli("write", "9")
        self.assertEqual(code, 1)
        self.assertIn("错误", err)

    def test_history_and_restore(self):
        self.run_cli("init", "--title", "A")
        self.run_cli("plan", "--chapters", "2")
        self.run_cli("write", "1")
        self.run_cli("write", "1")
        code, out, _ = self.run_cli("history", "1")
        self.assertEqual(code, 0)
        self.assertIn("共 2 稿", out)
        code, out, _ = self.run_cli("history", "1", "--restore", "1")
        self.assertEqual(code, 0)
        self.assertIn("已回滚", out)

    def test_prompts_and_doctor(self):
        code, out, _ = self.run_cli("prompts")
        self.assertEqual(code, 0)
        self.assertIn("draft", out)
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("mock", out)

    def test_accept(self):
        self.run_cli("init", "--title", "A")
        self.run_cli("plan", "--chapters", "2")
        self.run_cli("write", "1")
        code, out, _ = self.run_cli("accept", "1")
        self.assertEqual(code, 0)
        self.assertIn("已定稿", out)
        self.assertIn("新增 canon 事实", out)


if __name__ == "__main__":
    unittest.main()
