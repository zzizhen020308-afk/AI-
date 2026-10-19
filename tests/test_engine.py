import json
import tempfile
import unittest
from pathlib import Path

from novel_loop import engine
from novel_loop.config import load_llm_config
from novel_loop.llm import MockProvider
from novel_loop.prompts import PromptStore
from novel_loop.store import Project, StoreError


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "novel"
        self.project = Project.create(self.root, title="雨夜收据")
        self.prompts = PromptStore.locate(self.root)
        self.provider = MockProvider()

    def make_chapters(self, count=3):
        return engine.plan(self.project, self.provider, self.prompts, count)


class TestStore(ProjectTestCase):
    def test_templates_are_created(self):
        for rel in (
            "brief/background.md",
            "brief/premise.md",
            "brief/style.md",
            "brief/characters/_模板.md",
            "memory/canon.md",
            "project.json",
        ):
            self.assertTrue((self.root / rel).is_file(), rel)

    def test_load_rejects_plain_directory(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        with self.assertRaises(StoreError):
            Project.load(empty)

    def test_revision_numbering_and_draft_sync(self):
        chapters = self.make_chapters(2)
        first = chapters[0]
        self.assertEqual(self.project.write_revision(first, "甲稿"), 1)
        self.assertEqual(self.project.write_revision(first, "乙稿"), 2)
        self.assertEqual(self.project.revisions(1), [1, 2])
        self.assertEqual(self.project.draft(1), "乙稿")
        self.assertEqual(self.project.read_revision(1, 1), "甲稿")
        self.assertEqual(self.project.chapter(1).current_revision, 2)

    def test_restore_revision(self):
        chapters = self.make_chapters(2)
        self.project.write_revision(chapters[0], "甲稿")
        self.project.write_revision(chapters[0], "乙稿")
        self.project.restore_revision(1, 1)
        self.assertEqual(self.project.draft(1), "甲稿")
        self.assertEqual(self.project.chapter(1).current_revision, 1)

    def test_feedback_roundtrip(self):
        self.project.add_feedback(1, "结尾太软")
        self.project.add_feedback(1, "第二段删掉")
        texts = [item["text"] for item in self.project.feedbacks(1)]
        self.assertEqual(texts, ["结尾太软", "第二段删掉"])

    def test_append_canon_keeps_header_once(self):
        self.project.append_canon(["主角右手有伤"])
        self.project.append_canon(["收据被烧掉了"])
        canon = self.project.canon()
        self.assertEqual(canon.count("# 已确立的事实"), 1)
        self.assertIn("主角右手有伤", canon)
        self.assertIn("收据被烧掉了", canon)

    def test_chapter_lookup_error(self):
        with self.assertRaises(StoreError):
            self.project.chapter(9)


class TestPrompts(ProjectTestCase):
    def test_all_templates_parse(self):
        for name in ("intake", "outline", "draft", "revise", "critique", "accept"):
            system, user = self.prompts.load(name)
            self.assertTrue(system, name)
            self.assertTrue(user, name)

    def test_render_replaces_known_placeholders_only(self):
        system, user = self.prompts.render(
            "draft", {"chapter_number": "7", "chapter_title": "断线", "target_words": "3000"}
        )
        self.assertIn("第 7 章《断线》", user)
        self.assertIn("3000", system)
        self.assertNotIn("{{chapter_title}}", user)
        # 没传的变量原样保留，方便发现漏填
        self.assertIn("{{background}}", user)


class TestEngine(ProjectTestCase):
    def test_plan_builds_chapters_with_beats(self):
        chapters = self.make_chapters(4)
        self.assertEqual(len(chapters), 4)
        self.assertEqual([c.number for c in chapters], [1, 2, 3, 4])
        self.assertTrue(all(c.beats for c in chapters))
        self.assertTrue(all(c.status == "planned" for c in chapters))
        self.assertEqual(len(self.project.chapters()), 4)

    def test_write_round_saves_revision_and_critique(self):
        self.make_chapters(3)
        result = engine.write_round(self.project, self.provider, self.prompts, 1)
        self.assertEqual(result.revision, 1)
        self.assertTrue(result.draft)
        self.assertTrue(result.critique.questions)
        self.assertEqual(self.project.chapter(1).status, "in_review")
        self.assertEqual(self.project.chapter(1).rounds, 1)
        self.assertEqual(self.project.revisions(1), [1])
        self.assertEqual(self.project.critique(1, 1)["overall"], result.critique.overall)

    def test_second_round_goes_through_revise_template(self):
        self.make_chapters(3)
        engine.write_round(self.project, self.provider, self.prompts, 1)
        self.project.add_feedback(1, "结尾要更狠一点")
        second = engine.write_round(self.project, self.provider, self.prompts, 1)
        self.assertEqual(second.revision, 2)
        self.assertIn("修订稿", second.draft)

    def test_accept_writes_summary_and_canon(self):
        self.make_chapters(3)
        engine.write_round(self.project, self.provider, self.prompts, 1)
        data = engine.accept(self.project, self.provider, self.prompts, 1)
        self.assertTrue(data["summary"])
        self.assertEqual(self.project.chapter(1).status, "accepted")
        self.assertIn(data["summary"], self.project.summary(1))
        for fact in data["canon"]:
            self.assertIn(fact, self.project.canon())

    def test_accept_requires_draft(self):
        self.make_chapters(3)
        with self.assertRaises(engine.NovelError):
            engine.accept(self.project, self.provider, self.prompts, 1)

    def test_summaries_only_include_earlier_accepted_chapters(self):
        self.make_chapters(3)
        for number in (1, 2):
            engine.write_round(self.project, self.provider, self.prompts, number)
            engine.accept(self.project, self.provider, self.prompts, number)
        context = engine.build_context(self.project, self.project.chapter(3))
        self.assertIn("第 1 章", context["summaries"])
        self.assertIn("第 2 章", context["summaries"])
        self.assertNotIn("第 3 章", context["summaries"])

    def test_run_loop_with_feedback_then_accept(self):
        self.make_chapters(3)
        answers = iter(["把结尾改得更狠", "a"])
        said = []
        final = engine.run_loop(
            self.project,
            self.provider,
            self.prompts,
            1,
            ask=lambda prompt: next(answers),
            say=said.append,
            render=lambda result: "",
        )
        self.assertEqual(final, "accepted")
        self.assertEqual(self.project.chapter(1).rounds, 2)
        self.assertEqual(self.project.chapter(1).current_revision, 2)
        self.assertEqual(len(self.project.feedbacks(1)), 1)
        self.assertTrue(self.project.summary(1))

    def test_run_loop_respects_round_limit(self):
        self.make_chapters(3)
        final = engine.run_loop(
            self.project,
            self.provider,
            self.prompts,
            1,
            ask=lambda prompt: "再改",
            say=lambda text: None,
            render=lambda result: "",
            max_rounds=2,
        )
        self.assertEqual(final, "in_review")
        self.assertEqual(self.project.chapter(1).rounds, 2)

    def test_auto_fix_triggers_only_on_critical_issue(self):
        self.make_chapters(3)
        result = engine.write_round(self.project, self.provider, self.prompts, 1)
        self.assertFalse(result.auto_fixed)  # mock 只报 minor


class TestJsonExtraction(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(engine.extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_block(self):
        self.assertEqual(engine.extract_json('```json\n{"a": 2}\n```'), {"a": 2})

    def test_prose_around_object(self):
        text = '好的，这是大纲：\n{"chapters": [{"title": "一"}]}\n希望有用。'
        self.assertEqual(engine.extract_json(text)["chapters"][0]["title"], "一")

    def test_invalid_raises(self):
        with self.assertRaises(engine.NovelError):
            engine.extract_json("没有 JSON")


class TestConfig(unittest.TestCase):
    def test_without_key_uses_mock(self):
        config = load_llm_config(env={})
        self.assertTrue(config.is_mock)

    def test_with_key_uses_openai_compatible(self):
        config = load_llm_config(env={"NOVEL_API_KEY": "sk-test", "NOVEL_MODEL": "m1"})
        self.assertEqual(config.provider, "openai-compatible")
        self.assertEqual(config.model, "m1")
        self.assertIn("sk-t", config.describe())

    def test_env_overrides_base_url(self):
        config = load_llm_config(env={"NOVEL_API_KEY": "k", "NOVEL_API_BASE": "http://localhost:11434/v1/"})
        self.assertEqual(config.base_url, "http://localhost:11434/v1")


if __name__ == "__main__":
    unittest.main()
