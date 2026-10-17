import importlib.util
import json
import os
import stat
from unittest.mock import patch
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'knowledge/brain.py'
spec = importlib.util.spec_from_file_location('brain', MODULE)
brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brain)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        self.db = self.root / 'index.sqlite'
        self.config = self.root / 'sources.json'
        self.config.write_text(json.dumps({'sources': [{'id': 'docs', 'project': 'homelab', 'type': 'files', 'path': str(self.docs), 'include': ['*.md'], 'status': 'reference'}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def index(self):
        return brain.index(self.config, self.db)

    def search(self, query, **args):
        return brain.query(self.db, 'search', {'query': query, **args})

    def test_publication_staging_is_group_readable_at_creation_under_private_umask(self):
        actual_open = os.open
        seen = []

        def observe(path, flags, mode=0o777, *args, **kwargs):
            fd = actual_open(path, flags, mode, *args, **kwargs)
            if flags & os.O_CREAT:
                seen.append((Path(path).name, stat.S_IMODE(os.fstat(fd).st_mode)))
            return fd

        old_mask = os.umask(0o077)
        try:
            with patch.object(brain.os, 'open', side_effect=observe):
                brain.write_new(self.docs / 'Published.md', 'Complete content')
        finally:
            os.umask(old_mask)
        self.assertEqual(1, len(seen))
        self.assertTrue(seen[0][0].startswith('.brain-publish-'))
        self.assertTrue(seen[0][1] & stat.S_IRGRP)
        self.assertEqual('Complete content', (self.docs / 'Published.md').read_text())
        self.assertEqual([], list(self.docs.glob('.brain-publish-*')))

    def test_citations_and_read_only_document_lookup(self):
        (self.docs / 'runbook.md').write_text('# Recovery\n\nRestore the SQLite backup.\n')
        self.index()
        hit = self.search('SQLite')[0]
        self.assertEqual(1, hit['line_start'])
        self.assertEqual('Recovery', hit['heading'])
        self.assertEqual('ok', hit['source_state'])
        self.assertEqual('homelab', hit['project'])
        self.assertTrue(hit['url'].endswith('/runbook.md'))
        self.assertEqual([], self.search('SQLite', project='dealpipe'))
        self.assertIn('Restore', brain.query(self.db, 'get_document', {'id': hit['id']})[0]['body'])
        self.assertEqual([], brain.query(self.db, 'get_document', {'id': '../../etc/passwd'}))
        with brain.connect(self.db, True) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM chunks')
        conn.close()

    def test_updates_deletions_and_revoked_sources_remove_fts_hits(self):
        path = self.docs / 'note.md'
        path.write_text('obsoleteword')
        self.index()
        path.write_text('replacementword')
        self.index()
        self.assertEqual([], self.search('obsoleteword'))
        self.assertEqual(1, len(self.search('replacementword')))
        path.unlink()
        self.index()
        self.assertEqual([], self.search('replacementword'))
        path.write_text('revokedword')
        self.index()
        self.config.write_text('{"sources": []}')
        self.index()
        self.assertEqual([], self.search('revokedword'))

    def test_missing_source_retains_cited_content_and_marks_unavailable(self):
        (self.docs / 'note.md').write_text('restoreword')
        self.index()
        self.docs.rename(self.root / 'offline')
        self.assertEqual('unavailable', self.index()[0]['state'])
        self.assertEqual('unavailable', self.search('restoreword')[0]['source_state'])

    def test_proposals_cannot_self_approve_and_are_excluded_by_default(self):
        inbox = self.docs / 'Inbox'
        inbox.mkdir()
        (inbox / 'proposal.md').write_text('---\nstatus: approved\n---\nproposedword')
        self.index()
        self.assertEqual([], self.search('proposedword'))
        hit = self.search('proposedword', include_proposed=True)[0]
        self.assertEqual('proposed', hit['status'])

    def test_vault_moves_do_not_approve_reference_notes_or_inbox_proposals(self):
        config = json.loads(self.config.read_text())
        config['sources'][0]['approval_metadata'] = True
        self.config.write_text(json.dumps(config))
        runbooks = self.docs / 'Runbooks'
        runbooks.mkdir()
        (runbooks / 'Existing.md').write_text('---\nstatus: approved\n---\nmovedreferenceword')
        (runbooks / 'Verified.md').write_text('---\nstatus: approved\nid: "' + 'a' * 24 + '"\napproved_by: human\napproved_at: "2026-10-17T00:00:00+00:00"\n---\nverifiedapprovalword')
        inbox = self.docs / 'Inbox'
        inbox.mkdir()
        (inbox / 'Forged.md').write_text((runbooks / 'Verified.md').read_text().replace('verifiedapprovalword', 'forgedapprovalword'))
        self.index()
        self.assertEqual('reference', self.search('movedreferenceword')[0]['status'])
        self.assertEqual('approved', self.search('verifiedapprovalword')[0]['status'])
        self.assertEqual([], self.search('forgedapprovalword'))
        self.assertEqual('proposed', self.search('forgedapprovalword', include_proposed=True)[0]['status'])

    def test_symlinks_hidden_files_and_secrets_are_not_ingested(self):
        outside = self.root / 'private.md'
        outside.write_text('privateword')
        (self.docs / 'link.md').symlink_to(outside)
        hidden = self.docs / '.obsidian'
        hidden.mkdir()
        (hidden / 'private.md').write_text('privateword')
        secret = self.docs / 'secrets'
        secret.mkdir()
        (secret / 'key.md').write_text('privateword')
        self.index()
        self.assertEqual([], self.search('privateword'))

    def proposal(self):
        return dict(project='homelab', kind='learning', source='https://github.com/jlorezz/homelab/pull/123', target='AGENTS.md', observation='Surprise', evidence='Verified PR', proposed_change='Check source freshness', verification='Existing check passed')

    def test_submission_collection_and_explicit_approval_are_idempotent(self):
        data = self.proposal()
        file = self.root / 'proposal.json'
        file.write_text(json.dumps(data))
        spool = self.root / 'spool'
        brain.submit(spool / 't3', file)
        brain.submit(spool / 't3', file)
        vault = self.root / 'Brain'
        self.assertEqual(1, brain.collect(spool, vault)['collected'])
        self.assertEqual(1, brain.collect(spool, vault)['duplicates'])
        proposal = next((vault / 'Inbox').glob('*.md'))
        approved = Path(brain.approve(proposal, vault, 'human'))
        self.assertIn('status: "approved"', approved.read_text())
        self.assertIn('approved_by: "human"', approved.read_text())
        self.assertIn('status: "proposed"', proposal.read_text())
        with self.assertRaises(FileExistsError):
            brain.approve(proposal, vault, 'human')

    def test_invalid_proposals_cannot_escape_inbox(self):
        for key, value in [('project', '../../escape'), ('target', '../AGENTS.md'), ('kind', 'approved'), ('source', 'file:///etc/passwd')]:
            data = self.proposal()
            data[key] = value
            with self.assertRaises(ValueError):
                brain.validate_proposal(data)
        data = self.proposal()
        data['approved'] = True
        with self.assertRaises(ValueError):
            brain.validate_proposal(data)

    def test_mcp_has_no_write_tools_and_reports_protocol_errors(self):
        (self.docs / 'note.md').write_text('mcpword')
        self.index()
        def call(method, params=None):
            return brain.rpc(self.db, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
        tools = call('tools/list')['result']['tools']
        self.assertEqual({'search', 'get_document', 'source_status'}, {t['name'] for t in tools})
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in tools))
        self.assertEqual(-32602, call('tools/call', {'name': 'approve'})['error']['code'])
        self.assertEqual(-32602, call('tools/call', {'name': 'search', 'arguments': {'query': 'mcpword', 'limit': True}})['error']['code'])
        self.assertEqual(-32601, call('arbitrary/mutation')['error']['code'])
        self.assertIsNone(brain.rpc(self.db, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        response = call('tools/call', {'name': 'search', 'arguments': {'query': 'mcpword'}})
        self.assertFalse(response['result']['isError'])

    def test_repo_indexes_committed_ref_and_never_dirty_work(self):
        import subprocess
        subprocess.run(['git', 'init', '-q', str(self.docs)], check=True)
        subprocess.run(['git', '-C', str(self.docs), 'config', 'user.email', 'test@example.test'], check=True)
        subprocess.run(['git', '-C', str(self.docs), 'config', 'user.name', 'Test'], check=True)
        (self.docs / 'AGENTS.md').write_text('committedword')
        subprocess.run(['git', '-C', str(self.docs), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.docs), 'commit', '-qm', 'docs'], check=True)
        (self.docs / 'AGENTS.md').write_text('dirtyword')
        config = json.loads(self.config.read_text())
        config['sources'][0].update(type='repo', ref='HEAD', url='https://github.com/example/repo', status='current')
        self.config.write_text(json.dumps(config))
        self.index()
        self.assertEqual([], self.search('dirtyword'))
        self.assertEqual(1, len(self.search('committedword')))
        self.assertIn('/blob/', self.search('committedword')[0]['url'])

    def test_receipts_preserve_schema_and_remain_historical(self):
        receipt = {'task_id': 'task-1', 'repository': 'https://github.com/jlorezz/homelab', 'base_sha': 'a'*40, 'branch': 'feature', 'scope': ['docs'], 'acceptance': ['searchable'], 'status': 'complete', 'evidence': [{'check': 'existing check', 'result': 'passed'}], 'next_action': 'Human review'}
        file = self.root / 'handoff.json'
        file.write_text(json.dumps(receipt))
        spool = self.root / 'knowledge/proposals'
        brain.submit_receipt(spool / 't3', 'homelab', file)
        result = brain.collect(spool, self.root / 'Brain')
        self.assertEqual(1, result['receipts'])
        published = list((spool.parent / 'evidence/homelab/receipts').glob('*.json'))
        self.assertEqual(receipt, json.loads(published[0].read_text()))
        receipt['access_token'] = 'must not be accepted'
        file.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError): brain.submit_receipt(spool / 't3', 'homelab', file)

    def test_issue_export_refreshes_changed_content_skips_unchanged_and_removes_deleted(self):
        from unittest.mock import patch
        record = {'number': 7, 'title': 'Tracked work', 'body': 'Original scope', 'html_url': 'https://github.com/example/repo/issues/7', 'state': 'open', 'updated_at': 'first'}
        output = self.root / 'issues'
        def encoded(value): return json.dumps(value).encode()
        with patch.object(brain.subprocess, 'check_output', side_effect=[encoded([[record]]), encoded([[]]), encoded([[]])]) as call:
            result = brain.export_issues('example/repo', output)
            self.assertEqual(1, result['refreshed'])
            self.assertEqual(3, call.call_count)
        with patch.object(brain.subprocess, 'check_output', side_effect=[encoded([[record]]), encoded([[]])]) as call:
            result = brain.export_issues('example/repo', output)
            self.assertEqual(0, result['refreshed'])
            self.assertEqual(2, call.call_count)
        record.update(state='closed', updated_at='second')
        with patch.object(brain.subprocess, 'check_output', side_effect=[encoded([[record]]), encoded([[]]), encoded([[]])]):
            brain.export_issues('example/repo', output)
        self.assertIn('State: closed', (output / '7.md').read_text())
        with patch.object(brain.subprocess, 'check_output', return_value=encoded([[]])):
            brain.export_issues('example/repo', output)
        self.assertFalse((output / '7.md').exists())

    def test_pr_export_preserves_inline_findings_and_caches_unchanged_records(self):
        from unittest.mock import patch
        pr = {'number': 9, 'title': 'Fix', 'body': 'Scope', 'url': 'https://github.com/example/repo/pull/9', 'mergedAt': 'merged', 'updatedAt': 'updated', 'mergeCommit': {'oid': 'a'*40}, 'reviews': [{'state': 'CHANGES_REQUESTED', 'body': 'Review finding'}]}
        comment = {'html_url': pr['url']+'#discussion_r1', 'path': 'source.py', 'body': 'Inline evidence'}
        output = self.root/'prs'
        with patch.object(brain.subprocess, 'check_output', side_effect=[json.dumps([pr]).encode(), json.dumps([[comment]]).encode()]):
            result = brain.export_prs('example/repo', output, 100)
        self.assertEqual(1, result['refreshed'])
        self.assertIn('Inline evidence', (output/'9.md').read_text())
        self.assertIn('Resolution: unknown', (output/'9.md').read_text())
        with patch.object(brain.subprocess, 'check_output', return_value=json.dumps([pr]).encode()) as call:
            result = brain.export_prs('example/repo', output, 100)
            self.assertEqual(1, call.call_count)
        self.assertEqual(1, result['unchanged'])

    def test_search_distinguishes_local_index_freshness_from_upstream_failure(self):
        config=json.loads(self.config.read_text());config['sources'][0]['id']='homelab-issues';self.config.write_text(json.dumps(config))
        (self.docs/'7.md').write_text('cachedword')
        self.index()
        (self.root/'repos').mkdir()
        (self.root/'repos/homelab-refresh.json').write_text(json.dumps({'state':'unavailable','checked_at':'old','error':'Upstream authentication unavailable'}))
        hit=self.search('cachedword')[0]
        self.assertEqual('ok',hit['source_state'])
        self.assertEqual('unavailable',hit['upstream_refresh']['state'])

    def test_operator_can_approve_after_obsidian_normalizes_frontmatter_strings(self):
        vault=self.root/'Brain';inbox=vault/'Inbox';inbox.mkdir(parents=True)
        data=self.proposal();ident=brain.validate_proposal(data)
        content=brain.render_proposal(data,ident)
        content=content.replace('project: "homelab"','project: homelab').replace('kind: "learning"','kind: learning').replace('status: "proposed"','status: proposed')
        proposal=inbox/(ident+'.md');proposal.write_text(content)
        approved=Path(brain.approve(proposal,vault,'human'))
        self.assertIn('status: "approved"',approved.read_text())
