"""Authorization and crash-recovery boundaries; no live GitHub writes."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("coordinator", Path(__file__).resolve().parents[1] / "coordinator.py")
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)
URL = "https://github.com/jlorezz/homelab/issues/7"
CONFIG = {"schema_version": 1, "coordinator_thread_id": "thread-home", "enabled_after": "2026-10-17T00:00:00Z",
          "approvers": ["jlorezz"], "repositories": {
              "jlorezz/homelab": {"base": "main", "project_owner": "jlorezz", "project_number": 3,
                                   "checkout": "/checkout", "coordinator_thread_id": "thread-home"}}}


def comment(comment_id=10, body="/t3 deliver", login="jlorezz", kind="User", **overrides):
    return {"id": comment_id, "body": body, "user": {"login": login, "type": kind},
            "issue_url": "https://api.github.com/repos/jlorezz/homelab/issues/7",
            "created_at": "2026-10-17T10:00:00Z", "updated_at": "2026-10-17T10:00:00Z", **overrides}


class FakeGitHub:
    def __init__(self):
        self.entries = [comment()]
        self.calls = []
        self.items = [{"id": "item", "content": {"type": "Issue", "url": URL}}]
        self.post_failure = False
        self.edit_failure = False
        self.issue_state = "open"
        self.on_edit = None
        self.pr_state = {"state": "MERGED", "mergedAt": "2026-10-17T11:00:00Z"}

    def __call__(self, *args, payload=None):
        self.calls.append((args, payload))
        if args == ("api", "user"):
            return {"login": "jlorezz"}
        if args[:2] == ("api", "graphql"):
            query = next(value for value in args if value.startswith("query="))
            if "repositoryOwner" in query:
                return {"data": {"repositoryOwner": {"projectV2": {"id": "project"}}}}
            if "fields(first:" in query:
                return {"data": {"node": {"fields": {"nodes": [
                    {"name": "Status", "id": "status", "options": [
                        {"id": name, "name": name} for name in set(queue.STATUS.values())]}], "totalCount": 1}}}}
            return {"data": {"node": {"items": {
                "nodes": [{"id": item["id"], "content": {"__typename": "Issue", "url": item["content"]["url"]}}
                          for item in self.items],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        if args[:2] == ("project", "item-edit"):
            if self.edit_failure:
                raise queue.QueueError("edit failed")
            if self.on_edit:
                self.on_edit(args[args.index("--single-select-option-id") + 1])
            return {}
        if args[:2] == ("api", "--paginate"):
            if "?state=open" in args[-1]:
                return [[{"html_url": URL, "title": "Example", "state": "open"}]] if self.issue_state == "open" else [[]]
            return [self.entries[:100], self.entries[100:]]
        if args[:3] == ("api", "--method", "POST"):
            if self.post_failure:
                raise queue.QueueError("post outcome unknown")
            self.entries.append(comment(1000 + len(self.entries), payload["body"]))
            return {"html_url": URL + "#issuecomment-1000"}
        if args == ("api", "repos/jlorezz/homelab/issues/7"):
            return {"state": self.issue_state, "title": "Example"}
        if args[:2] == ("pr", "view"):
            return self.pr_state
        raise AssertionError(f"unexpected gh call: {args}")


class AuthorizationTests(unittest.TestCase):
    def test_exact_command_from_human_after_enable_only(self):
        self.assertEqual(queue.latest_command(CONFIG, [comment()])["id"], 10)
        for body in (" /t3 deliver", "/t3 deliver\n", "Please /t3 deliver", "```\n/t3 deliver\n```", "/t3 deliver\nextra"):
            self.assertIsNone(queue.latest_command(CONFIG, [comment(body=body)]))
        for entry in (comment(login="attacker"), comment(kind="Bot"),
                      comment(updated_at="2026-10-17T10:01:00Z"),
                      comment(created_at=CONFIG["enabled_after"], updated_at=CONFIG["enabled_after"])):
            self.assertIsNone(queue.latest_command(CONFIG, [entry]))

    def test_cancellation_and_new_authorization_order_by_id(self):
        cancel = comment(11, "/t3 cancel")
        self.assertEqual(queue.latest_command(CONFIG, [cancel, comment()])["body"], "/t3 cancel")
        self.assertEqual(queue.latest_command(CONFIG, [cancel, comment(12)])["id"], 12)

    def test_forged_and_malformed_receipts_are_not_authoritative(self):
        receipt = {"schema_version": 1, "issue": URL, "coordinator_thread_id": "thread-home",
                   "authorization_comment_id": 10, "phase": "claimed"}
        body = "<!-- t3-coordinator:v1 " + json.dumps(receipt) + " -->"
        self.assertEqual(len(queue.receipts(CONFIG, [comment(body=body)], "jlorezz", URL)), 1)
        for entry in (comment(body=body, login="attacker"), comment(body=body + "\nquoted"),
                      comment(body="<!-- t3-coordinator:v1 {invalid} -->"),
                      comment(body=body.replace("thread-home", "another-thread"))):
            self.assertEqual(queue.receipts(CONFIG, [entry], "jlorezz", URL), [])


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "new/state.json"
        self.fake = FakeGitHub()
        self.mock = patch.object(queue, "gh", self.fake)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def args(self, phase="claimed", **kwargs):
        return argparse.Namespace(issue=URL, authorization_comment_id=10, phase=phase,
                                  task_id=kwargs.get("task_id"), pr=kwargs.get("pr"), summary="Verified progress")

    def test_scan_is_read_only_and_reads_all_comment_pages(self):
        self.fake.entries = [comment(n, "untrusted issue text") for n in range(1, 110)] + [comment(110)]
        result = queue.scan(CONFIG, self.state)
        self.assertEqual(result["candidates"][0]["authorization_comment_id"], 110)
        self.assertFalse(self.state.parent.exists())
        self.assertFalse(any("POST" in call[0] or "item-edit" in call[0] for call in self.fake.calls))

    def test_api_failures_abort_scan(self):
        with patch.object(queue, "gh", side_effect=queue.QueueError("API unavailable")):
            with self.assertRaisesRegex(queue.QueueError, "unavailable"):
                queue.scan(CONFIG, self.state)

    def test_claim_consumes_authorization_and_updates_status(self):
        record = queue.receipt(CONFIG, self.state, self.args())
        self.assertFalse(record["pending"])
        result = queue.scan(CONFIG, self.state)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["active"][0]["receipt"]["phase"], "claimed")
        edits = [call[0] for call in self.fake.calls if call[0][:2] == ("project", "item-edit")]
        self.assertIn("In Progress", edits[0])
        with self.assertRaisesRegex(queue.QueueError, "already consumed"):
            queue.receipt(CONFIG, self.state, self.args())

    def test_unknown_post_outcome_blocks_repeat_until_explicit_recovery(self):
        self.fake.post_failure = True
        with self.assertRaisesRegex(queue.QueueError, "unknown"):
            queue.receipt(CONFIG, self.state, self.args())
        result = queue.scan(CONFIG, self.state)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["interrupted_claims"]), 1)
        self.fake.post_failure = False
        with self.assertRaisesRegex(queue.QueueError, "interrupted"):
            queue.receipt(CONFIG, self.state, self.args("running"))
        args = self.args()
        args.reason = "Confirmed no worker or remote receipt; retain consumption"
        queue.recover(CONFIG, self.state, args)
        self.assertEqual(queue.scan(CONFIG, self.state)["candidates"], [])
        queue.receipt(CONFIG, self.state, self.args("blocked"))

    def test_project_write_failure_keeps_claim_interrupted(self):
        self.fake.edit_failure = True
        with self.assertRaisesRegex(queue.QueueError, "edit failed"):
            queue.receipt(CONFIG, self.state, self.args())
        self.assertTrue(next(iter(queue.read_state(self.state).values()))["pending"])
        self.assertEqual(len(self.fake.entries), 2)  # receipt posted, status uncertain

    def test_cancel_revokes_progress_and_is_reported_for_active_task(self):
        queue.receipt(CONFIG, self.state, self.args())
        self.fake.entries.append(comment(20, "/t3 cancel"))
        with self.assertRaisesRegex(queue.QueueError, "superseded or cancelled"):
            queue.receipt(CONFIG, self.state, self.args("running"))
        result = queue.scan(CONFIG, self.state)
        self.assertTrue(result["active"][0]["cancel_requested"])
        queue.receipt(CONFIG, self.state, self.args("cancelled"))
        self.assertEqual(queue.scan(CONFIG, self.state)["active"], [])

    def test_new_deliver_requeues_but_edited_command_does_not(self):
        queue.receipt(CONFIG, self.state, self.args())
        self.fake.entries.append(comment(20, updated_at="2026-10-17T10:01:00Z"))
        self.assertEqual(queue.scan(CONFIG, self.state)["candidates"], [])
        self.fake.entries.append(comment(21))
        result = queue.scan(CONFIG, self.state)
        self.assertEqual(result["candidates"][0]["authorization_comment_id"], 21)
        self.assertEqual(result["active"][0]["authorization_comment_id"], 10)
        self.assertFalse(result["active"][0]["authorization_valid"])
        args = self.args()
        args.authorization_comment_id = 21
        with self.assertRaisesRegex(queue.QueueError, "previous authorization"):
            queue.receipt(CONFIG, self.state, args)

    def test_closed_issue_and_missing_authorization_keep_active_claim_visible(self):
        queue.receipt(CONFIG, self.state, self.args())
        self.fake.issue_state = "closed"
        self.fake.entries = []
        result = queue.scan(CONFIG, self.state)
        self.assertFalse(result["active"][0]["issue_open"])
        self.assertFalse(result["active"][0]["authorization_valid"])
        queue.receipt(CONFIG, self.state, self.args("cancelled"))
        self.assertEqual(queue.scan(CONFIG, self.state)["active"], [])

    def test_remote_receipt_survives_local_state_loss_and_closed_issue(self):
        queue.receipt(CONFIG, self.state, self.args())
        self.state.unlink()
        self.fake.issue_state = "closed"
        result = queue.scan(CONFIG, self.state)
        self.assertEqual(result["active"][0]["receipt"]["phase"], "claimed")

    def test_scan_uses_repository_wide_comment_pagination(self):
        queue.scan(CONFIG, self.state)
        endpoints = [args[-1] for args, _ in self.fake.calls if args[:2] == ("api", "--paginate")]
        self.assertTrue(any("issues/comments?since=" in endpoint for endpoint in endpoints))
        self.assertFalse(any("issues/7/comments" in endpoint for endpoint in endpoints))

    def test_closed_issue_cannot_be_claimed(self):
        self.fake.issue_state = "closed"
        self.assertEqual(queue.scan(CONFIG, self.state)["candidates"], [])
        with self.assertRaisesRegex(queue.QueueError, "closed"):
            queue.receipt(CONFIG, self.state, self.args())

    def test_existing_status_required_before_any_write(self):
        with patch.object(queue, "status_target", side_effect=queue.QueueError("Status missing")):
            with self.assertRaisesRegex(queue.QueueError, "Status missing"):
                queue.receipt(CONFIG, self.state, self.args())
        self.assertFalse(self.state.exists())

    def test_done_requires_merged_pr_and_never_closes_issue(self):
        queue.receipt(CONFIG, self.state, self.args())
        with self.assertRaisesRegex(queue.QueueError, "require --pr"):
            queue.receipt(CONFIG, self.state, self.args("done"))
        pr = "https://github.com/jlorezz/homelab/pull/8"
        queue.receipt(CONFIG, self.state, self.args("done", pr=pr))
        self.assertEqual(queue.scan(CONFIG, self.state)["active"], [])
        self.assertFalse(any(call[0][:3] == ("issue", "close", URL) for call in self.fake.calls))

    def test_repository_filter_and_scoped_thread(self):
        config = copy.deepcopy(CONFIG)
        config["coordinator_thread_id"] = "other-default"
        result = queue.scan(config, self.state, "jlorezz/homelab")
        self.assertEqual(result["coordinator_thread_id"], "thread-home")
        with self.assertRaisesRegex(queue.QueueError, "configured repository"):
            queue.scan(CONFIG, self.state, "unexpected/repo")

    def test_progress_preserves_task_and_pr_on_local_and_remote_recovery(self):
        queue.receipt(CONFIG, self.state, self.args())
        pr = "https://github.com/jlorezz/homelab/pull/8"
        queue.receipt(CONFIG, self.state, self.args("running", task_id="worker-123", pr=pr))
        self.state.unlink()
        record = queue.receipt(CONFIG, self.state, self.args("blocked"))
        self.assertEqual(record["task_id"], "worker-123")
        self.assertEqual(record["pr"], pr)
        record = queue.receipt(CONFIG, self.state, self.args("review"))
        self.assertEqual(record["task_id"], "worker-123")
        self.assertEqual(record["pr"], pr)

    def test_null_scoped_thread_fails_closed(self):
        config = copy.deepcopy(CONFIG)
        config["repositories"]["jlorezz/homelab"]["coordinator_thread_id"] = None
        with self.assertRaisesRegex(queue.QueueError, "nonempty string"):
            queue.scan(config, self.state, "jlorezz/homelab")
        config_file = Path(self.temporary.name) / "config.json"
        config_file.write_text(json.dumps(config))
        with self.assertRaisesRegex(queue.QueueError, "nonempty string"):
            queue.read_config(config_file)


class BoardTests(unittest.TestCase):
    setUp = QueueTests.setUp
    args = QueueTests.args

    def setUp(self):
        QueueTests.setUp(self)
        self.config = {**copy.deepcopy(CONFIG), "authorization_mode": "project_ready"}
        self.board = Path(self.temporary.name) / "board.json"
        self.snapshot = {"item": {"issue": URL, "status": "Backlog", "updated_at": "2026-10-17T10:00:00Z"}}
        self.fake.entries = []
        self.fake.on_edit = lambda status: self.move(status, "2026-10-17T11:30:00Z")
        self.snapshot_patch = patch.object(queue, "board_snapshot", side_effect=lambda *_: copy.deepcopy(self.snapshot))
        self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)

    def move(self, status, at="2026-10-17T11:00:00Z"):
        self.snapshot["item"].update(status=status, updated_at=at)

    def initialize(self):
        return queue.board_update(self.config, self.board, "jlorezz/homelab", initialize=True)

    def tick(self):
        queue.board_update(self.config, self.board, "jlorezz/homelab")
        return queue.scan(self.config, self.state, "jlorezz/homelab", self.board)

    def board_args(self, phase="claimed", auth_id=None):
        args = self.args(phase)
        args.authorization_comment_id = None
        args.authorization_id = auth_id or "ready:item:2026-10-17T11:00:00Z"
        args.board_file = self.board
        return args

    def test_baseline_existing_ready_never_authorizes_itself(self):
        self.move("Ready", "2026-10-17T10:00:00Z")
        self.initialize()
        self.assertEqual(self.tick()["candidates"], [])
        with self.assertRaisesRegex(queue.QueueError, "already initialized"):
            self.initialize()
        self.move("Backlog")
        self.tick()
        self.move("Ready", "2026-10-17T12:00:00Z")
        candidate = self.tick()["candidates"][0]
        self.assertEqual(candidate["authorization_id"], "ready:item:2026-10-17T12:00:00Z")
        self.assertEqual(candidate["authorization_source"], "project_ready")
        self.assertNotIn("actor", candidate)

    def test_no_baseline_fails_closed(self):
        with self.assertRaisesRegex(queue.QueueError, "board-init"):
            self.tick()

    def test_ready_event_is_stable_until_claimed_and_claim_deduplicates(self):
        self.initialize()
        self.move("Ready")
        first = self.tick()["candidates"][0]
        second = self.tick()["candidates"][0]
        self.assertEqual(first["authorization_id"], second["authorization_id"])
        board = queue.read_state(self.board)["repositories"]["jlorezz/homelab"]
        self.assertEqual(len(board["events"]), 1)
        queue.receipt(self.config, self.state, self.board_args())
        self.assertEqual(self.tick()["candidates"], [])
        with self.assertRaisesRegex(queue.QueueError, "already consumed"):
            queue.receipt(self.config, self.state, self.board_args())

    def test_coordinator_status_progress_does_not_cancel_and_hold_pauses(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        self.move("In Progress", "2026-10-17T12:00:00Z")
        active = self.tick()["active"][0]
        self.assertFalse(active["cancel_requested"])
        self.assertTrue(active["authorization_valid"])
        queue.receipt(self.config, self.state, self.board_args("running"))
        self.move("On Hold", "2026-10-17T13:00:00Z")
        active = self.tick()["active"][0]
        self.assertTrue(active["paused"])
        self.assertFalse(active["cancel_requested"])
        with self.assertRaisesRegex(queue.QueueError, "paused"):
            queue.receipt(self.config, self.state, self.board_args("running"))
        queue.receipt(self.config, self.state, self.board_args("blocked"))

    def test_backlog_or_canceled_revokes_active_board_authorization(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        for status in ("Backlog", "Canceled"):
            self.move(status, "2026-10-17T12:00:00Z")
            active = self.tick()["active"][0]
            self.assertTrue(active["cancel_requested"])
            with self.assertRaisesRegex(queue.QueueError, "revoked"):
                queue.receipt(self.config, self.state, self.board_args("running"))
        queue.receipt(self.config, self.state, self.board_args("cancelled"))
        self.assertEqual(self.tick()["active"], [])

    def test_claim_revalidates_latest_ready_field_timestamp(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        self.move("Ready", "2026-10-17T12:00:00Z")
        with self.assertRaisesRegex(queue.QueueError, "revoked"):
            queue.receipt(self.config, self.state, self.board_args())

    def test_new_ready_while_active_cannot_claim_second_worker(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        self.move("In Progress", "2026-10-17T12:00:00Z")
        self.tick()
        self.move("Ready", "2026-10-17T13:00:00Z")
        result = self.tick()
        self.assertEqual(len(result["active"]), 1)
        self.assertEqual(len(result["candidates"]), 1)
        with self.assertRaisesRegex(queue.QueueError, "previous authorization"):
            queue.receipt(self.config, self.state, self.board_args(auth_id="ready:item:2026-10-17T13:00:00Z"))

    def test_comment_cancel_revokes_board_event(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        self.fake.entries.append(comment(15, "/t3 cancel", created_at="2026-10-17T12:00:00Z", updated_at="2026-10-17T12:00:00Z"))
        self.assertEqual(self.tick()["candidates"], [])
        with self.assertRaisesRegex(queue.QueueError, "cancelled"):
            queue.receipt(self.config, self.state, self.board_args())

    def test_newest_authorization_across_comment_and_board_sources_wins(self):
        self.initialize()
        self.fake.entries.append(comment())  # older comment fallback
        self.move("Ready")
        result = self.tick()
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("authorization_id", result["candidates"][0])
        legacy = self.args()
        legacy.board_file = self.board
        with self.assertRaisesRegex(queue.QueueError, "superseded"):
            queue.receipt(self.config, self.state, legacy)
        self.fake.entries.append(comment(20, created_at="2026-10-17T12:00:00Z", updated_at="2026-10-17T12:00:00Z"))
        result = self.tick()
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["authorization_comment_id"], 20)
        with self.assertRaisesRegex(queue.QueueError, "superseded"):
            queue.receipt(self.config, self.state, self.board_args())

    def test_historic_cancel_does_not_revoke_newer_ready_authorization(self):
        self.initialize()
        self.fake.entries.append(comment(15, "/t3 cancel"))  # 10:00, before Ready
        self.move("Ready")  # 11:00
        self.assertEqual(len(self.tick()["candidates"]), 1)
        queue.receipt(self.config, self.state, self.board_args())
        self.move("In Progress", "2026-10-17T12:00:00Z")
        active = self.tick()["active"][0]
        self.assertFalse(active["cancel_requested"])
        self.assertTrue(active["authorization_valid"])
        queue.receipt(self.config, self.state, self.board_args("running"))

    def test_comment_fallback_claim_from_backlog_obeys_later_board_controls(self):
        self.initialize()
        self.fake.entries.append(comment())
        args = self.args()
        args.board_file = self.board
        queue.receipt(self.config, self.state, args)  # explicit comment may claim Backlog
        self.assertEqual(self.snapshot["item"]["status"], "In Progress")
        self.move("On Hold", "2026-10-17T12:00:00Z")
        active = self.tick()["active"][0]
        self.assertTrue(active["paused"])
        self.assertFalse(active["cancel_requested"])
        args.phase = "running"
        with self.assertRaisesRegex(queue.QueueError, "paused"):
            queue.receipt(self.config, self.state, args)
        for status in ("Backlog", "Canceled"):
            self.move(status, "2026-10-17T13:00:00Z")
            active = self.tick()["active"][0]
            self.assertTrue(active["cancel_requested"])
            self.assertFalse(active["authorization_valid"])
            with self.assertRaisesRegex(queue.QueueError, "pause and reconcile"):
                queue.receipt(self.config, self.state, args)

    def test_human_done_or_review_requires_reconciliation(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        for status in ("Done", "In Review"):
            self.move(status, "2026-10-17T13:00:00Z")
            active = self.tick()["active"][0]
            self.assertTrue(active["paused"])
            self.assertFalse(active["cancel_requested"])
            self.assertFalse(active["authorization_valid"])
            with self.assertRaisesRegex(queue.QueueError, "revoked"):
                queue.receipt(self.config, self.state, self.board_args("running"))

    def test_automatic_done_after_merge_allows_only_verified_done_receipt(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        delivered = self.board_args("delivered")
        delivered.pr = "https://github.com/jlorezz/homelab/pull/8"
        queue.receipt(self.config, self.state, delivered)
        self.move("Done", "2026-10-17T13:00:00Z")
        self.fake.issue_state = "closed"  # GitHub may auto-close on merge
        active = self.tick()["active"][0]
        self.assertTrue(active["paused"])
        self.assertFalse(active["authorization_valid"])
        self.fake.pr_state = {"state": "OPEN", "mergedAt": None}
        with self.assertRaisesRegex(queue.QueueError, "confirmed merged PR"):
            queue.receipt(self.config, self.state, self.board_args("done"))
        local = next(iter(queue.read_state(self.state).values()))
        self.assertEqual(local["phase"], "delivered")
        self.assertFalse(local["pending"])
        self.fake.pr_state = {"state": "MERGED", "mergedAt": "2026-10-17T13:00:00Z"}
        record = queue.receipt(self.config, self.state, self.board_args("done"))
        self.assertEqual(record["phase"], "done")
        self.assertEqual(record["pr"], delivered.pr)
        self.assertEqual(self.tick()["active"], [])

    def test_requeued_ready_survives_cancellation_of_old_blocked_task(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        queue.receipt(self.config, self.state, self.board_args("blocked"))
        self.assertEqual(self.snapshot["item"]["status"], "On Hold")
        self.tick()
        self.move("Ready", "2026-10-17T14:00:00Z")
        result = self.tick()
        self.assertEqual(len(result["active"]), 1)
        self.assertEqual(len(result["candidates"]), 1)
        edits_before = sum(call[0][:2] == ("project", "item-edit") for call in self.fake.calls)
        record = queue.receipt(self.config, self.state, self.board_args("cancelled"))
        self.assertEqual(record["phase"], "cancelled")
        self.assertEqual(record["project_status_preserved"], "Ready")
        self.assertEqual(self.snapshot["item"]["status"], "Ready")
        self.assertEqual(sum(call[0][:2] == ("project", "item-edit") for call in self.fake.calls), edits_before)
        result = self.tick()
        self.assertEqual(result["active"], [])
        self.assertEqual(len(result["candidates"]), 1)
        queue.receipt(self.config, self.state, self.board_args(auth_id="ready:item:2026-10-17T14:00:00Z"))
        self.assertEqual(self.snapshot["item"]["status"], "In Progress")

    def test_ordinary_cancellation_still_updates_project_canceled(self):
        self.initialize()
        self.move("Ready")
        self.tick()
        queue.receipt(self.config, self.state, self.board_args())
        record = queue.receipt(self.config, self.state, self.board_args("cancelled"))
        self.assertIsNone(record["project_status_preserved"])
        self.assertEqual(self.snapshot["item"]["status"], "Canceled")


class TransportTests(unittest.TestCase):
    def test_graphql_errors_with_success_exit_code_fail_closed(self):
        response = {"data": {"repositoryOwner": None}, "errors": [
            {"type": "RATE_LIMIT", "message": "API rate limit already exceeded"}]}
        result = subprocess.CompletedProcess([], 0, json.dumps(response), "")
        with patch.object(queue.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(queue.QueueError, "RATE_LIMIT.*rate limit already exceeded"):
                queue.project_id(CONFIG["repositories"]["jlorezz/homelab"])

    def test_paginated_transport_passes_argument_array_without_shell(self):
        result = subprocess.CompletedProcess([], 0, stdout='[[{"id":1}],[{"id":2}]]', stderr="")
        with patch.object(queue.subprocess, "run", return_value=result) as run:
            self.assertEqual([c["id"] for c in queue.comments("owner/repo", 7)], [1, 2])
        self.assertEqual(run.call_args.args[0], ["gh", "api", "--paginate", "--slurp",
                                               "repos/owner/repo/issues/7/comments?per_page=100"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_subprocess_failure_and_invalid_json_are_errors(self):
        failure = subprocess.CalledProcessError(1, ["gh"], stderr="network unavailable")
        with patch.object(queue.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(queue.QueueError, "network unavailable"):
                queue.gh("api", "user")
        with patch.object(queue.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "invalid", "")):
            with self.assertRaisesRegex(queue.QueueError, "invalid JSON"):
                queue.gh("api", "user")


class ProjectReadTests(unittest.TestCase):
    def setUp(self):
        self.settings = CONFIG["repositories"]["jlorezz/homelab"]

    def page(self, nodes, more=False, cursor=None):
        return {"data": {"node": {"items": {"nodes": nodes, "pageInfo": {
            "hasNextPage": more, "endCursor": cursor}}}}}

    def item(self, item_id="item", url=URL, status="Ready", updated="2026-10-17T11:00:00Z"):
        return {"id": item_id, "content": {"__typename": "Issue", "url": url},
                "fieldValueByName": {"name": status, "updatedAt": updated}}

    def test_owner_lookup_supports_user_and_organization_without_cli_inference(self):
        response = {"data": {"repositoryOwner": {"projectV2": {"id": "project"}}}}
        for owner in ("jlorezz", "dealpipe"):
            with self.subTest(owner=owner), patch.object(queue, "gh", return_value=response) as gh:
                self.assertEqual(queue.project_id({**self.settings, "project_owner": owner}), "project")
                args = gh.call_args.args
                self.assertEqual(args[:2], ("api", "graphql"))
                self.assertIn(f"owner={owner}", args)
                query = next(value for value in args if value.startswith("query="))
                self.assertIn("... on Organization", query)
                self.assertIn("... on User", query)

    def test_missing_owner_or_inaccessible_project_fails_closed(self):
        for owner in (None, {"projectV2": None}):
            with patch.object(queue, "gh", return_value={"data": {"repositoryOwner": owner}}):
                with self.assertRaisesRegex(queue.QueueError, "project is unavailable"):
                    queue.project_id(self.settings)

    def test_project_items_paginate_and_ignore_non_issues(self):
        first = self.page([self.item(), {"id": "draft", "content": None}], True, "next")
        second = self.page([self.item("second", URL + "0"),
                            {"id": "pr", "content": {"__typename": "PullRequest"}}])
        with patch.object(queue, "project_id", return_value="project"), \
                patch.object(queue, "gh", side_effect=[first, second]) as gh:
            items = queue.project_items(self.settings)
        self.assertEqual([item["id"] for item in items], ["item", "second"])
        self.assertEqual(items[1]["content"], {"type": "Issue", "url": URL + "0"})
        self.assertIn("cursor=next", gh.call_args.args)

    def test_pagination_must_advance_and_all_pages_must_succeed(self):
        cases = ([self.page([], True, None)],
                 [self.page([], True, "same"), self.page([], True, "same")],
                 [self.page([self.item()], True, "next"), queue.QueueError("rate limited")])
        for pages in cases:
            with patch.object(queue, "project_id", return_value="project"), \
                    patch.object(queue, "gh", side_effect=pages):
                with self.assertRaises(queue.QueueError):
                    queue.project_items(self.settings)

    def test_inaccessible_project_node_fails_closed(self):
        with patch.object(queue, "project_id", return_value="project"), \
                patch.object(queue, "gh", return_value={"data": {"node": None}}):
            with self.assertRaisesRegex(queue.QueueError, "project is unavailable"):
                queue.project_items(self.settings)

    def test_snapshot_filters_repository_and_keeps_status_timestamp(self):
        nodes = [self.item(), self.item("other", "https://github.com/other/repo/issues/1"),
                 {"id": "draft", "content": None}]
        with patch.object(queue, "project_id", return_value="project"), \
                patch.object(queue, "gh", return_value=self.page(nodes)):
            self.assertEqual(queue.board_snapshot(CONFIG, "jlorezz/homelab"), {
                "item": {"issue": URL, "status": "Ready", "updated_at": "2026-10-17T11:00:00Z"}})

    def test_snapshot_rejects_status_without_timestamp(self):
        with patch.object(queue, "project_id", return_value="project"), \
                patch.object(queue, "gh", return_value=self.page([self.item(updated=None)])):
            with self.assertRaisesRegex(queue.QueueError, "timestamp unavailable"):
                queue.board_snapshot(CONFIG, "jlorezz/homelab")

    def test_partial_snapshot_never_changes_authorization_history(self):
        config = {**copy.deepcopy(CONFIG), "authorization_mode": "project_ready"}
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.json"
            queue.board_update(config, board, "jlorezz/homelab", initialize=True, snapshot={})
            original = board.read_bytes()
            with patch.object(queue, "project_id", return_value="project"), \
                    patch.object(queue, "gh", side_effect=[self.page([self.item()], True, "next"),
                                                          queue.QueueError("rate limited")]):
                with self.assertRaisesRegex(queue.QueueError, "rate limited"):
                    queue.board_update(config, board, "jlorezz/homelab")
            self.assertEqual(board.read_bytes(), original)

    def test_status_lookup_requires_complete_fields_and_existing_option(self):
        status = {"id": "status", "name": "Status", "options": [{"id": "progress", "name": "In Progress"}]}
        with patch.object(queue, "project_id", return_value="project"), \
                patch.object(queue, "gh", return_value={"data": {"node": {"fields": {
                    "totalCount": 2, "nodes": [{}, status]}}}}):
            self.assertEqual(queue.status_target(self.settings, "claimed"), ("project", "status", "progress"))
            with self.assertRaisesRegex(queue.QueueError, "existing project Status option"):
                queue.status_target(self.settings, "done")
        with patch.object(queue, "project_id", return_value="project"), \
                patch.object(queue, "gh", return_value={"data": {"node": {"fields": {
                    "totalCount": 101, "nodes": [status]}}}}):
            with self.assertRaisesRegex(queue.QueueError, "incomplete"):
                queue.status_target(self.settings, "claimed")

    def test_tick_reuses_single_snapshot_without_additional_project_reads(self):
        config = {**copy.deepcopy(CONFIG), "authorization_mode": "project_ready"}
        backlog = {"item": {"issue": URL, "status": "Backlog", "updated_at": "2026-10-17T10:00:00Z"}}
        ready = {"item": {"issue": URL, "status": "Ready", "updated_at": "2026-10-17T11:00:00Z"}}
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.json"
            state = Path(directory) / "state.json"
            queue.board_update(config, board, "jlorezz/homelab", initialize=True, snapshot=backlog)
            fake = FakeGitHub()
            fake.entries = []
            argv = ["coordinator.py", "--board-file", str(board), "--state-file", str(state),
                    "board-tick", "--repository", "jlorezz/homelab"]
            with patch.object(queue.sys, "argv", argv), patch.object(queue, "gh", fake), \
                    patch.object(queue, "board_snapshot", return_value=ready) as snapshot, \
                    patch("builtins.print") as output:
                self.assertEqual(queue.main(), 0)
            snapshot.assert_called_once()
            result = json.loads(output.call_args.args[0])
            self.assertEqual(result["candidates"][0]["authorization_id"],
                             "ready:item:2026-10-17T11:00:00Z")
            self.assertFalse(any(call[0][:2] in (("api", "graphql"), ("project", "item-list"))
                                 for call in fake.calls))


if __name__ == "__main__":
    unittest.main()
