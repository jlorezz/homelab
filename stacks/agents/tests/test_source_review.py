import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

SPEC = importlib.util.spec_from_file_location('source_review', Path(__file__).parents[1] / 'knowledge/source_review.py')
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class SourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'source.md').write_text('original')
        self.commit()
        self.pinned = self.git('rev-parse', 'HEAD')
        self.brain = self.root / 'Brain'
        (self.brain / 'Projects/test').mkdir(parents=True)
        self.note = self.brain / 'Projects/test/Overview.md'
        self.note.write_text(f'[source](https://github.com/fixture/repo/blob/{self.pinned}/source.md)')
        self.config = {'sources': [{'type': 'repo', 'url': 'https://github.com/fixture/repo',
                                  'path': str(self.repo), 'project': 'test', 'ref': 'refs/heads/main'}]}
        self.health = self.repo.parent / 'test-refresh.json'
        self.health.write_text(json.dumps({'state': 'ok', 'checked_at': datetime.now(timezone.utc).isoformat()}))

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')

    def test_unrelated_commit_does_not_flag_summary(self):
        (self.repo / 'other.md').write_text('unrelated')
        self.commit()
        self.assertEqual(review.inspect(self.config, self.brain)['findings'], [])

    def test_changed_source_deduplicates_and_new_citation_clears(self):
        (self.repo / 'source.md').write_text('changed')
        self.commit()
        state = self.root / 'review/state.json'
        result = review.run(self.config, self.brain, state)
        self.assertEqual(len(result['findings']), 1)
        queue = self.brain / 'Inbox/Source Review.md'
        before = queue.stat().st_mtime_ns
        review.run(self.config, self.brain, state)
        self.assertEqual(queue.stat().st_mtime_ns, before)
        self.note.write_text(self.note.read_text().replace(self.pinned, self.git('rev-parse', 'HEAD')))
        self.assertEqual(review.run(self.config, self.brain, state)['findings'], [])

    def test_deleted_source_is_reported(self):
        (self.repo / 'source.md').unlink()
        self.commit()
        self.assertEqual(review.inspect(self.config, self.brain)['findings'][0]['change'], 'deleted')

    def test_failed_or_missing_upstream_is_not_clean(self):
        self.health.unlink()
        result = review.inspect(self.config, self.brain)
        self.assertEqual(len(result['errors']), 1)
        self.health.write_text(json.dumps({'state': 'unavailable'}))
        self.assertEqual(len(review.inspect(self.config, self.brain)['errors']), 1)

    def test_cannot_overwrite_human_inbox_note(self):
        (self.brain / 'Inbox').mkdir()
        queue = self.brain / 'Inbox/Source Review.md'
        queue.write_text('human note')
        with self.assertRaises(ValueError):
            review.run(self.config, self.brain, self.root / 'state.json')
        self.assertEqual(queue.read_text(), 'human note')
