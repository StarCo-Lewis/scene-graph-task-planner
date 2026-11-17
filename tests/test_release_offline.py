"""Offline tests for the Scene Graph Task Planner release.

Run from the release root:  python -m unittest discover -s tests -v

Nothing here contacts a model, a robot or the network. In this process, sockets and
urllib are patched to raise. The worker's alignment subprocess is not patched; it
only ever runs with a non-live backend (offline, synthetic or recorded), which never
reaches the original transport. Every CLI call runs inside ``patch.dict(os.environ)``
with all ``SGTP_*`` variables removed first, so no setting leaks between tests.

All "synthetic" plans come from ``sgtp.synthetic_backend``, an exact-label test
double. They show that the copied pipeline runs end to end; they are not model results.
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embodied_pipeline import _release  # noqa: E402
from sgtp import cli, lock, synthetic_backend, toy  # noqa: E402

NETWORK_ATTEMPTS = []
_GUARDS = []
SCOPE = "sgtp-offline-test-scope"
EXPECTED_VISITS = [10, 11, 10]
CHANGED_INSTRUCTION = "Visit the blue marker."
FORMAL_SKILL_COUNT = 12
FORMAL_WITHHELD_COUNT = 18
INTERNAL_PATH_MARKERS = ("C:/Users", "C:\\\\Users", "C:\\Users", "D:/", "D:\\\\", "/mnt/c/",
                         "NebulaAI")
ENGINE_DIR = ROOT / "vendor" / "legacy_engines"


def _forbid_network(*args, **kwargs):
    NETWORK_ATTEMPTS.append(repr(args)[:200])
    raise AssertionError("Network access attempted in an offline test")


def setUpModule():
    for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open",
                   "socket.socket.connect", "socket.create_connection"):
        guard = patch(target, _forbid_network)
        guard.start()
        _GUARDS.append(guard)


def tearDownModule():
    while _GUARDS:
        _GUARDS.pop().stop()


def run_cli(argv):
    """Run ``python -m sgtp`` in-process with a clean SGTP_* environment."""
    argv = [str(item) for item in argv]
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ):
        for key in [k for k in os.environ if k.startswith("SGTP_")]:
            del os.environ[key]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def element_ids(value):
    found = []
    if isinstance(value, dict):
        if isinstance(value.get("element_id"), int):
            found.append(value["element_id"])
        for key in sorted(value):
            if key != "element_id":
                found.extend(element_ids(value[key]))
    elif isinstance(value, list):
        for item in value:
            found.extend(element_ids(item))
    return found


def action_sequence(out_dir):
    """(skill_id, bound element ids) for every step of the delivered plan, in order."""
    plan = read_json(Path(out_dir) / "worker" / "skill_plan.json")
    return [(step["skill_id"], element_ids(step["arguments"]))
            for subtask in plan["subtask_plans"] for step in subtask["steps"]]


def plan_args(scene_dir, out_dir, *extra):
    return ["plan", "--scene-dir", scene_dir, "--instruction", toy.INSTRUCTION,
            "--out-dir", out_dir] + list(extra)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def log_entries(out_dir):
    path = Path(out_dir) / "backend_log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sgtp_test_")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def toy(self, variant="base"):
        out = self.tmp / ("toy_" + variant)
        toy.write_toy(out, variant)
        return out

    def fresh_copy(self, recording):
        """Copy a recording to a new path. Replay cursors are kept per (path, stage) in
        each process and the parent process serves the matrix stage, so every replay in
        one test process needs its own file."""
        self._copies = getattr(self, "_copies", 0) + 1
        target = self.tmp / ("recording_copy_%d.json" % self._copies)
        shutil.copyfile(recording, target)
        return target.resolve()

    def record_toy_run(self):
        """Synthetic run of the base toy task under SCOPE, turned into a recording."""
        source = self.tmp / "recorded_source_run"
        code, _o, err = run_cli(plan_args(ROOT / "examples" / "toy_scene", source,
                                          "--backend", "synthetic", "--plan-scope", SCOPE))
        self.assertEqual(code, 0, err)
        recording = self.tmp / "recorded.json"
        code, _o, err = run_cli(["record", "--backend-log", source / "backend_log.jsonl",
                                 "--out", recording])
        self.assertEqual(code, 0, err)
        return recording


class NetworkGuardTests(unittest.TestCase):
    def test_urlopen_is_blocked_in_this_process(self):
        with self.assertRaises(AssertionError):
            urllib.request.urlopen("https://example.invalid/")
        with self.assertRaises(AssertionError):
            socket.create_connection(("example.invalid", 443))


class ToyInputTests(TempDirTestCase):
    def test_static_example_equals_the_generator(self):
        example = ROOT / "examples" / "toy_scene"
        self.assertEqual(read_json(example / "scene_graph.json"), toy.nodes())
        self.assertEqual(read_json(example / "scene_graph_edges.json"), toy.edges())
        self.assertEqual(read_json(example / "scene_metadata.json"), toy.metadata())

    def test_generated_scene_files_equal_the_generator(self):
        out = self.toy()
        self.assertEqual(read_json(out / "scene" / "scene_graph.json"), toy.nodes())
        manifest = read_json(out / "toy_manifest.json")
        self.assertTrue(manifest["synthetic"])
        self.assertIn("overlay.json", manifest["files"])
        self.assertNotIn("prebinding_ambiguous.json", manifest["files"])

    def test_ambiguous_variant_adds_a_second_blue_marker(self):
        out = self.toy("ambiguous")
        blue = [n["id"] for n in read_json(out / "scene" / "scene_graph.json")
                if n["object_tag"] == "blue marker"]
        self.assertEqual(blue, [11, 13])
        self.assertTrue((out / "prebinding_ambiguous.json").is_file())

    def test_existing_directory_and_unknown_variant_are_refused(self):
        out = self.toy()
        with self.assertRaises(FileExistsError):
            toy.write_toy(out)
        with self.assertRaises(ValueError):
            toy.nodes("no_such_variant")

    def test_overlay_marks_the_straddling_wall_panel_unknown(self):
        out = self.toy()
        index = {e["object_id"]: e for e in read_json(out / "overlay.json")["object_region_index"]}
        self.assertEqual(index[12]["status"], "unknown")
        self.assertEqual(index[10]["region_id"], "region_a")
        self.assertEqual(index[11]["region_id"], "region_c")


class TopologyChainMixin:
    def topology(self, *argv):
        code, _out, _err = run_cli(["topology"] + list(argv))
        return code

    def prepare(self, run_dir, toy_dir):
        return self.topology("prepare", "--run-dir", run_dir,
                             "--scene-dir", toy_dir / "scene",
                             "--overlay", toy_dir / "overlay.json",
                             "--instruction", toy.INSTRUCTION,
                             "--record-id", "sgtp_toy_0001",
                             "--start-region-id", toy.START_REGION)

    def ingest(self, run_dir, document):
        return self.topology("ingest-binding", "--run-dir", run_dir,
                             "--from-file", document, "--source", "manual_fixture")

    def full_chain(self, toy_dir, run_dir):
        self.assertEqual(self.prepare(run_dir, toy_dir), 0)
        self.assertEqual(self.ingest(run_dir, toy_dir / "prebinding_bound.json"), 0)
        self.assertEqual(self.topology("route", "--run-dir", run_dir), 0)
        self.assertEqual(self.topology("slice", "--run-dir", run_dir), 0)
        self.assertEqual(self.topology("handoff", "--run-dir", run_dir), 0)


class TopologyTests(TopologyChainMixin, TempDirTestCase):
    def test_base_chain_routes_by_hop_count(self):
        run = self.tmp / "run"
        self.full_chain(self.toy(), run)
        plan = read_json(run / "route" / "route_plan.json")
        self.assertTrue(plan["complete"])
        self.assertEqual([leg["hop_count"] for leg in plan["legs"]], [0, 2, 2])
        self.assertEqual(plan["total_hop_count"], 4)
        self.assertEqual(plan["ordered_waypoint_object_ids"], EXPECTED_VISITS)
        self.assertFalse(plan["metric_distance_computed"])
        self.assertTrue((run / "handoff" / "task.json").is_file())
        self.assertTrue((run / "slice" / "scene" / "scene_graph.json").is_file())

    def test_ambiguous_prebinding_stops_with_a_question(self):
        toy_dir, run = self.toy("ambiguous"), self.tmp / "run"
        self.assertEqual(self.prepare(run, toy_dir), 0)
        self.assertEqual(self.ingest(run, toy_dir / "prebinding_ambiguous.json"), 3)
        envelope = read_json(run / "ingest_binding" / "engineering_plan.json")
        self.assertIsNone(envelope["plan"])
        self.assertEqual(envelope["artifact_completeness"], "incomplete")
        self.assertFalse((run / "route").exists())

    def test_closed_portal_leaves_no_route(self):
        toy_dir, run = self.toy("closed_ab"), self.tmp / "run"
        self.assertEqual(self.prepare(run, toy_dir), 0)
        self.assertEqual(self.ingest(run, toy_dir / "prebinding_bound.json"), 0)
        self.assertEqual(self.topology("route", "--run-dir", run), 3)
        plan = read_json(run / "route" / "route_plan.json")
        self.assertFalse(plan["complete"])
        self.assertEqual([p["portal_id"] for p in plan["excluded_closed_portals"]],
                         ["portal_ab"])
        envelope = read_json(run / "route" / "engineering_plan.json")
        self.assertIsNone(envelope["plan"])


class PlanTests(TopologyChainMixin, TempDirTestCase):
    def assert_complete_toy_plan(self, out, command="plan"):
        record = read_json(out / cli.RUN_RECORD)
        self.assertEqual(record["command"], command)
        self.assertEqual(record["artifact_completeness"], "complete")
        self.assertFalse(record["plan_is_null"])
        self.assertIs(record["model_invoked"], False)
        self.assertFalse(record["execution_performed"])
        envelope = read_json(out / "worker" / "engineering_plan.json")
        self.assertEqual(envelope["artifact_completeness"], "complete")
        self.assertIsNotNone(envelope["plan"])
        self.assertFalse(envelope["robot_executed"])
        sequence = action_sequence(out)
        self.assertEqual([skill for skill, _ids in sequence], ["navigate_to_object"] * 3)
        self.assertEqual([ids for _skill, ids in sequence], [[i] for i in EXPECTED_VISITS])
        return record, sequence

    def test_synthetic_plan_on_the_toy_scene_is_complete(self):
        out = self.tmp / "plan"
        code, _o, err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out,
                                          "--backend", "synthetic"))
        self.assertEqual(code, 0, err)
        record, _sequence = self.assert_complete_toy_plan(out)
        self.assertTrue(record["backend"]["synthetic_responder"])
        self.assertEqual(record["catalogue_profile"]["name"], _release.FORMAL_PROFILE)
        self.assertEqual(len(record["catalogue_profile"]["offered_skill_ids"]),
                         FORMAL_SKILL_COUNT)
        self.assertEqual(record["catalogue_profile"]["withheld_skill_count"],
                         FORMAL_WITHHELD_COUNT)
        gap = read_json(out / cli.GAP_RECORD)
        self.assertEqual(len(gap["withheld_catalogue_skills"]), FORMAL_WITHHELD_COUNT)
        self.assertEqual(record["backend_responses_served_without_model"], 3)

    def test_fixed_scope_runs_repeat_and_a_recording_replays(self):
        scene = ROOT / "examples" / "toy_scene"
        first, second, replay = self.tmp / "a", self.tmp / "b", self.tmp / "replay"
        for out in (first, second):
            code, _o, err = run_cli(plan_args(scene, out, "--backend", "synthetic",
                                              "--plan-scope", SCOPE))
            self.assertEqual(code, 0, err)
        _record, sequence = self.assert_complete_toy_plan(first)
        self.assertEqual(action_sequence(second), sequence)

        recording = self.tmp / "recorded.json"
        code, _o, err = run_cli(["record", "--backend-log", first / "backend_log.jsonl",
                                 "--out", recording])
        self.assertEqual(code, 0, err)
        document = read_json(recording)
        self.assertEqual(document["plan_scope"], SCOPE)
        self.assertEqual(sorted(document["responses"]),
                         ["decomposition", "object_binding", "skill_matching_matrix"])

        code, _o, err = run_cli(plan_args(scene, replay, "--backend",
                                          "recorded:" + str(self.fresh_copy(recording))))
        self.assertEqual(code, 0, err)
        record, replayed = self.assert_complete_toy_plan(replay)
        self.assertEqual(replayed, sequence)
        self.assertEqual(record["backend"]["plan_scope"], SCOPE)
        self.assertTrue(record["usable_as_plan"])
        self.assertEqual(document["format"], cli.backend_module().RECORDED_FORMAT)
        served = [e for e in log_entries(replay) if e["decision"] == "served_without_model"]
        self.assertEqual(len(served), 3)
        for entry in served:
            self.assertIs(entry["recorded_request_identity_matched"], True)
            expected = document["responses"][entry["stage"]][entry["recorded_index"]]
            self.assertEqual(entry["request_sha256"], expected["request_sha256"])

        mismatch = self.tmp / "mismatch"
        code, _o, _err = run_cli(plan_args(scene, mismatch, "--backend",
                                           "recorded:" + str(self.fresh_copy(recording)),
                                           "--plan-scope", "another-scope"))
        self.assertEqual(code, 2)
        self.assertFalse(mismatch.exists())

    def assert_refused_replay(self, out):
        record = read_json(out / cli.RUN_RECORD)
        self.assertEqual(record["artifact_completeness"], "incomplete")
        self.assertTrue(record["plan_is_null"])
        self.assertIsNot(record["usable_as_plan"], True)
        self.assertIs(record["model_invoked"], False)
        self.assertEqual(record["backend_responses_served_without_model"], 0)
        decisions = {d["decision"] for d in record["backend"]["decisions"]}
        self.assertIn("refused_recorded_replay", decisions)
        self.assertNotIn("served_without_model", decisions)
        envelope = read_json(out / "worker" / "engineering_plan.json")
        self.assertIsNone(envelope["plan"])
        return record

    def test_a_changed_instruction_does_not_reuse_the_recorded_answer(self):
        # Regression for the release probe: the same scope used to replay 10,11,10 for
        # "Visit the blue marker.".
        recording = self.record_toy_run()
        document = read_json(recording)
        out = self.tmp / "changed_instruction"
        code, _o, _err = run_cli(["plan", "--scene-dir", ROOT / "examples" / "toy_scene",
                                  "--instruction", CHANGED_INSTRUCTION, "--out-dir", out,
                                  "--backend", "recorded:" + str(self.fresh_copy(recording))])
        self.assertEqual(code, 3)
        self.assert_refused_replay(out)
        refused = [e for e in log_entries(out) if e["decision"] == "refused_recorded_replay"]
        self.assertTrue(refused)
        for entry in refused:
            self.assertEqual(entry["plan_scope"], SCOPE)
            recorded_hashes = {r["request_sha256"] for r in document["responses"][entry["stage"]]}
            self.assertNotIn(entry["request_sha256"], recorded_hashes)
            self.assertIn("not reused", entry["reason"])

    def test_a_changed_scene_does_not_reuse_the_recorded_answer(self):
        recording = self.record_toy_run()
        out = self.tmp / "changed_scene"
        code, _o, _err = run_cli(plan_args(self.toy("ambiguous") / "scene", out, "--backend",
                                           "recorded:" + str(self.fresh_copy(recording))))
        self.assertEqual(code, 3)
        self.assert_refused_replay(out)

    def test_an_older_unbound_recording_is_refused_before_anything_is_created(self):
        recording = self.record_toy_run()
        document = read_json(recording)
        old = self.tmp / "recorded_v1.json"
        old.write_text(json.dumps({
            "format": "sgtp_recorded_responses_v1", "plan_scope": SCOPE,
            "responses": {stage: [entry["response"] for entry in entries]
                          for stage, entries in document["responses"].items()}}),
            encoding="utf-8")
        out = self.tmp / "old_format"
        code, _o, err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out,
                                          "--backend", "recorded:" + str(old.resolve())))
        self.assertEqual(code, 2)
        self.assertIn("format", err)
        self.assertFalse(out.exists())

    def test_a_missing_recording_is_refused_before_anything_is_created(self):
        out = self.tmp / "missing_recording"
        code, _o, _err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out, "--backend",
                                           "recorded:" + str(self.tmp / "no_such.json")))
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_a_recording_from_another_catalogue_profile_is_refused(self):
        recording = self.record_toy_run()
        document = read_json(recording)
        self.assertEqual(document["catalogue_profile"], _release.FORMAL_PROFILE)
        other = self.tmp / "recorded_other_profile.json"
        other.write_text(json.dumps(dict(document,
                                         catalogue_profile=_release.HISTORICAL_PROFILE)),
                         encoding="utf-8")
        out = self.tmp / "other_profile"
        code, _o, err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out,
                                          "--backend", "recorded:" + str(other.resolve())))
        self.assertEqual(code, 2)
        self.assertIn("profile", err)
        self.assertFalse(out.exists())

    def test_a_log_without_request_identities_cannot_be_recorded(self):
        log = self.tmp / "old_log.jsonl"
        log.write_text(json.dumps({"decision": "served_without_model", "stage": "decomposition",
                                   "response": {}, "plan_scope": SCOPE,
                                   "catalogue_profile": _release.FORMAL_PROFILE}) + "\n",
                       encoding="utf-8")
        out = self.tmp / "recording_from_old_log.json"
        code, _o, err = run_cli(["record", "--backend-log", log, "--out", out])
        self.assertEqual(code, 2)
        self.assertIn("request identity", err)
        self.assertFalse(out.exists())

    def test_offline_default_is_incomplete_without_any_request(self):
        out = self.tmp / "offline"
        code, _o, _err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out))
        self.assertEqual(code, 3)
        record = read_json(out / cli.RUN_RECORD)
        self.assertEqual(record["backend"]["kind"], "offline")
        self.assertIs(record["model_invoked"], False)
        self.assertEqual(record["artifact_completeness"], "incomplete")
        envelope = read_json(out / "worker" / "engineering_plan.json")
        self.assertIsNone(envelope["plan"])
        decisions = {d["decision"] for d in record["backend"]["decisions"]}
        self.assertIn("refused_offline_default", decisions)
        self.assertNotIn("served_without_model", decisions)
        self.assertNotIn("live_request_delegated_to_original_transport", decisions)
        self.assertEqual(record["backend_responses_served_without_model"], 0)

    def test_live_needs_acknowledgement_and_creates_nothing(self):
        out = self.tmp / "live"
        code, _o, err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out,
                                          "--backend", "live", "--model", "m",
                                          "--base-url", "https://example.invalid/v1",
                                          "--api-key-env", "SGTP_TEST_UNSET_KEY"))
        self.assertEqual(code, 2)
        self.assertIn("acknowledge", err)
        self.assertFalse(out.exists())

    def test_live_with_acknowledgement_but_empty_key_creates_nothing(self):
        out = self.tmp / "live"
        code, _o, err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out,
                                          "--backend", "live", "--acknowledge-live-api",
                                          "--model", "m",
                                          "--base-url", "https://example.invalid/v1",
                                          "--api-key-env", "SGTP_TEST_UNSET_KEY"))
        self.assertEqual(code, 2)
        self.assertIn("empty", err)
        self.assertFalse(out.exists())

    def test_unknown_backend_is_a_usage_error(self):
        out = self.tmp / "unknown"
        code, _o, _err = run_cli(plan_args(ROOT / "examples" / "toy_scene", out,
                                           "--backend", "telepathy"))
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_ambiguous_scene_stays_incomplete(self):
        toy_dir, out = self.toy("ambiguous"), self.tmp / "plan"
        code, _o, _err = run_cli(plan_args(toy_dir / "scene", out, "--backend", "synthetic"))
        self.assertEqual(code, 3)
        record = read_json(out / cli.RUN_RECORD)
        self.assertEqual(record["artifact_completeness"], "incomplete")
        self.assertTrue(record["plan_is_null"])
        self.assertIs(record["model_invoked"], False)

    def test_plan_handoff_after_the_topology_chain(self):
        run, out = self.tmp / "run", self.tmp / "handoff_plan"
        self.full_chain(self.toy(), run)
        code, _o, err = run_cli(["plan-handoff", "--run-dir", run, "--out-dir", out,
                                 "--backend", "synthetic"])
        self.assertEqual(code, 0, err)
        record, _sequence = self.assert_complete_toy_plan(out, command="plan-handoff")
        self.assertEqual(record["task"]["input_scope"], read_json(
            run / "handoff" / "task.json").get("input_scope"))

    def test_plan_handoff_refuses_an_unfinished_topology_run(self):
        toy_dir, run, out = self.toy(), self.tmp / "run", self.tmp / "handoff_plan"
        self.assertEqual(self.prepare(run, toy_dir), 0)
        code, _o, _err = run_cli(["plan-handoff", "--run-dir", run, "--out-dir", out,
                                  "--backend", "synthetic"])
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())


class SyntheticResponderTests(unittest.TestCase):
    LABELS = ["blue marker", "red marker", "marker"]

    def test_mentions_are_ordered_longest_first_without_overlap(self):
        self.assertEqual(synthetic_backend.mentions(toy.INSTRUCTION, self.LABELS),
                         ["red marker", "blue marker", "red marker"])

    def test_no_exact_label_is_never_guessed(self):
        with self.assertRaises(synthetic_backend.SyntheticResponderError):
            synthetic_backend.visit_plan("Go to the kitchen.", self.LABELS)

    def test_unknown_request_shape_is_refused(self):
        with self.assertRaises(synthetic_backend.SyntheticResponderError):
            synthetic_backend.stage_of({"something": 1})


class BackendAdapterTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.backend = cli.backend_module()

    def write_recording(self, entries, scope="a", profile="p", name="recorded.json"):
        path = self.tmp / name
        path.write_text(json.dumps({"format": self.backend.RECORDED_FORMAT, "plan_scope": scope,
                                    "catalogue_profile": profile,
                                    "responses": {"decomposition": entries}}),
                        encoding="utf-8")
        return str(path)

    def recorded_env(self, scope="a", profile="p"):
        return patch.dict(os.environ, {self.backend.SCOPE_ENV: scope,
                                       self.backend.PROFILE_ENV: profile})

    def test_recorded_file_refuses_another_scope(self):
        body = b'{"a":1}'
        path = self.write_recording([{"request_sha256": sha256_bytes(body), "response": {}}])
        with self.recorded_env(scope="b"):
            with self.assertRaisesRegex(self.backend.BackendRefused, "plan scope"):
                self.backend._recorded(path, "decomposition",
                                       self.backend.request_identity(body))

    def test_identity_is_the_exact_request_bytes(self):
        body = b'{"a":1,"b":[1,2]}'
        self.assertEqual(self.backend.request_identity(body),
                         {"request_sha256": sha256_bytes(body)})
        # No canonicalisation: reordered keys, whitespace or list order are different requests.
        for other in (b'{"b":[1,2],"a":1}', b'{"a": 1,"b":[1,2]}', b'{"a":1,"b":[2,1]}'):
            self.assertNotEqual(self.backend.request_identity(other)["request_sha256"],
                                self.backend.request_identity(body)["request_sha256"])

    def test_recorded_response_is_served_only_for_its_own_request(self):
        body, changed = b'{"instruction":"red"}', b'{"instruction":"blue"}'
        path = self.write_recording([{"request_sha256": sha256_bytes(body),
                                      "response": {"answer": 1}}])
        with self.recorded_env():
            with self.assertRaisesRegex(self.backend.BackendRefused, "not reused"):
                self.backend._recorded(path, "decomposition",
                                       self.backend.request_identity(changed))
            # The refusal did not consume the entry.
            response, index, expected = self.backend._recorded(
                path, "decomposition", self.backend.request_identity(body))
        self.assertEqual((response, index, expected), ({"answer": 1}, 0, sha256_bytes(body)))

    def test_unbound_recorded_entries_are_refused(self):
        body = b'{"a":1}'
        entries = ({"response": {}}, {"request_sha256": None, "response": {}},
                   {"request_sha256": "x" * 64, "response": {}},
                   {"request_sha256": sha256_bytes(body)}, {})
        for number, entry in enumerate(entries):
            path = self.write_recording([entry], name="unbound_%d.json" % number)
            with self.recorded_env():
                with self.assertRaisesRegex(self.backend.BackendRefused, "not bound"):
                    self.backend._recorded(path, "decomposition",
                                           self.backend.request_identity(body))

    def test_older_format_and_other_profile_are_refused_by_the_adapter(self):
        body = b'{"a":1}'
        path = self.write_recording([{"request_sha256": sha256_bytes(body), "response": {}}])
        with self.recorded_env(profile="q"):
            with self.assertRaisesRegex(self.backend.BackendRefused, "catalogue profile"):
                self.backend._recorded(path, "decomposition",
                                       self.backend.request_identity(body))
        old = self.tmp / "old.json"
        old.write_text(json.dumps({"format": "sgtp_recorded_responses_v1", "plan_scope": "a",
                                   "catalogue_profile": "p",
                                   "responses": {"decomposition": [{}]}}), encoding="utf-8")
        with self.recorded_env():
            with self.assertRaisesRegex(self.backend.BackendRefused, "format"):
                self.backend._recorded(str(old), "decomposition",
                                       self.backend.request_identity(body))

    def test_a_log_line_without_a_request_hash_is_refused(self):
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps({"decision": "served_without_model",
                                   "stage": "decomposition", "response": {},
                                   "plan_scope": "a", "catalogue_profile": "p"}) + "\n",
                       encoding="utf-8")
        with self.assertRaisesRegex(self.backend.BackendRefused, "request identity"):
            self.backend.recorded_from_log(log)

    def test_a_random_scope_log_cannot_become_a_recording(self):
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps({"decision": "served_without_model",
                                   "stage": "decomposition", "response": {},
                                   "request_sha256": "0" * 64, "catalogue_profile": "p",
                                   "plan_scope": None}) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(self.backend.BackendRefused, "random plan scope"):
            self.backend.recorded_from_log(log)

    def test_live_without_acknowledgement_is_refused_by_the_adapter(self):
        request = urllib.request.Request("https://example.invalid/v1", data=b"{}")
        with patch.dict(os.environ, {self.backend.BACKEND_ENV: "live",
                                     self.backend.ACK_ENV: "", self.backend.LOG_ENV: ""}):
            with self.assertRaises(self.backend.BackendRefused):
                self.backend.route(request)

    def test_offline_is_the_default(self):
        request = urllib.request.Request("https://example.invalid/v1", data=b"{}")
        with patch.dict(os.environ, {self.backend.BACKEND_ENV: "",
                                     self.backend.LOG_ENV: ""}):
            self.assertEqual(self.backend.selected(), ("offline", ""))
            with self.assertRaises(self.backend.BackendRefused):
                self.backend.route(request)


class CatalogueProfileTests(unittest.TestCase):
    def test_formal_profile_offers_twelve_of_thirty(self):
        profile = _release.active_profile(_release.FORMAL_PROFILE)
        self.assertEqual(profile["catalogue_version"], "0.3.2")
        self.assertEqual(profile["catalogue_skill_count"], 30)
        self.assertEqual(len(profile["skill_ids"]), FORMAL_SKILL_COUNT)
        self.assertEqual(_release.DEFAULT_PROFILE, _release.FORMAL_PROFILE)

    def test_historical_profile_keeps_the_five_skill_subset(self):
        profile = _release.active_profile(_release.HISTORICAL_PROFILE)
        self.assertEqual(profile["catalogue_version"], "0.2.0")
        self.assertEqual(profile["catalogue_skill_count"], 33)
        self.assertEqual(len(profile["skill_ids"]), 5)
        self.assertIsNone(_release.catalogue_scope(profile))

    def test_unknown_profile_and_changed_asset_are_refused(self):
        with self.assertRaises(_release.CatalogueProfileError):
            _release.active_profile("no_such_profile")
        changed = dict(_release.PROFILES[_release.FORMAL_PROFILE], sha256="0" * 64)
        with patch.dict(_release.PROFILES, {_release.FORMAL_PROFILE: changed}):
            with self.assertRaises(_release.CatalogueProfileError):
                _release.active_profile(_release.FORMAL_PROFILE)

    def test_withheld_skills_match_the_compatibility_report(self):
        scope = _release.catalogue_scope(_release.active_profile(_release.FORMAL_PROFILE))
        report = read_json(_release.COMPATIBILITY_REPORT)
        withheld = [s["skill_id"] for s in report["skills"] if not s["offered_at_runtime"]]
        self.assertEqual([s["skill_id"] for s in scope["withheld_catalogue_skills"]], withheld)
        self.assertEqual(len(withheld), FORMAL_WITHHELD_COUNT)
        self.assertTrue(all(s["reasons"] for s in scope["withheld_catalogue_skills"]))
        self.assertEqual(scope["rule"], _release.WITHHELD_RULE)

    def test_catalogues_contain_no_internal_paths(self):
        for path in sorted((ROOT / "catalogues").rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            for marker in INTERNAL_PATH_MARKERS:
                self.assertNotIn(marker, text, path.name)


class ReleaseIntegrityTests(unittest.TestCase):
    def test_source_lock_passes(self):
        report = lock.verify(ROOT)
        self.assertEqual((report["changed"], report["missing"], report["unlisted"]), ([], [], []))
        self.assertTrue(report["passed"])

    def test_provenance_release_side_hashes_match(self):
        provenance = read_json(ROOT / "RELEASE_PROVENANCE.json")
        for entry in provenance["files"]:
            path = ROOT / entry["release_path"]
            self.assertTrue(path.is_file(), entry["release_path"])
            self.assertEqual(lock.sha256_file(path), entry["release_sha256"],
                             entry["release_path"])

    def test_release_code_is_loaded_only_from_this_tree(self):
        import embodied_pipeline.orchestration.task_collection_worker  # noqa: F401
        engine_names = {p.stem for p in ENGINE_DIR.rglob("*.py")}
        root = ROOT.resolve()
        for name, module in list(sys.modules.items()):
            top = name.split(".")[0]
            if top not in ("embodied_pipeline", "sgtp") and name not in engine_names:
                continue
            origin = getattr(module, "__file__", None)
            if origin is None:
                continue
            resolved = Path(origin).resolve()
            self.assertTrue(resolved == root or root in resolved.parents,
                            name + " loaded from " + str(resolved))


if __name__ == "__main__":
    unittest.main()
