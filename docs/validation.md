# Offline release validation

The publication source was validated on November 17, 2026 using native Windows Python 3.12.10 in a separate short-path copy, outside the original research workspace. No dependency installation was required; jsonschema was not installed in this interpreter.

- **44 tests passed**, 0 failures, 0 errors, 0 skips; unittest reported 5.781 seconds.
- The environment inherited no model credentials or proxies. An independent guard blocked network operations and non-Python child processes, including inside the alignment subprocess. No external guard event occurred. The unit suite also deliberately verifies its in-process network stubs.
- The genuine copied three-stage worker completed a synthetic ordered visit 10 → 11 → 10. The topology handoff, ambiguous-target stop and closed-portal route stop passed.
- Default offline refusal and live use without acknowledgment/key produce explicit stops or preflight errors.
- Four local Git filter checks preserved a CRLF Python file and the catalogue bytes with core.autocrlf both true and false. The ignore rules exclude generated runs and .env while allowing .env.example. No remote, commit or global Git configuration was used.
- All catalogue profiles, source locks, copied-file provenance and the loaded-module paths were checked. This confirms tested entry-point closure, not every unused legacy API.
- An additional independent regression probe confirmed that changing the instruction under the same replay scope produces exit 3, incomplete, plan:null and usable_as_plan:false. Identical request bodies remain replayable.

A pre-publication release-only replay implementation matched plan scope without matching request identity. The additional probe exposed an old-answer reuse (10 → 11 → 10 for a newly requested single visit to 11). It was repaired before publication by binding every recorded response to the exact request-body SHA-256; the current format is sgtp_recorded_responses_v2. No semantic validators were relaxed.

These are offline software checks with explicitly synthetic response fixtures. They are not new model results, robot execution, navigation accuracy, a physical safety certification or a rerun of the historical study. Historical results and their limitations are documented separately in [results.md](results.md).
