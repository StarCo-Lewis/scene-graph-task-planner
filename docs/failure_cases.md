# Representative historical failure cases

These are diagnostic summaries of saved November 9 results. They are not new experiments, physical failures, or claims that every current check has been fixed. Object numbers are local Scene200 identifiers. The public repository omits private raw logs and scene assets.

## 1. referent attachment

- **Configuration / grade:** Qwen 3.8 Flash / room recursive / D.
- **Tasks:** H09, H11, H12, H13.
- **Observed:** The model selected ornament 124 instead of the pedestal 110 described by the user.
- **Explanation:** The requested target and its descriptive landmark were conflated. The recorded relation 124 on 110 is a scene-model hypothesis rather than physically verified evidence.
- **Expected handling:** Resolve the target as the supporting pedestal and use the white ornament as a landmark; ask only if the supplied evidence leaves multiple defensible targets.
- **Limit:** Lexical cosine was recorded after the model proposal and did not automatically rebind the object; it is not evidence that cosine caused the error.

## 2. excessive clarification

- **Configuration / grade:** Qwen 3.8 Flash / room recursive / D.
- **Tasks:** H14.
- **Instruction:** I only need you beside the pedestal with the white ornament; nothing needs carrying.
- **Observed:** The response stopped to clarify alternatives after confusing a white ornament with a white platform supporting a black object.
- **Explanation:** The color modifier attached to the wrong noun and the target/landmark distinction was lost.
- **Expected handling:** Use the supplied description and support evidence to test alternatives before asking. The historical audit judged the alternatives unsupported under the supplied task evidence.
- **Limit:** This classification is relative to the supplied symbolic scene and review reference, not a claim of perfect real-world uniqueness.

## 3. binding wire schema

- **Configuration / grade:** Qwen 3.8 Flash / room recursive / C.
- **Tasks:** H15.
- **Observed:** Returned visit_id="visit_00" instead of the required visit_event_id="visit_001"; a bound response also retained a candidate list.
- **Explanation:** The local binding contract rejected the response before a complete plan could be delivered.
- **Expected handling:** Return the exact declared field names and mutually consistent bound/candidate states.
- **Limit:** The proposed object 136 agreed with the dialogue referent, but no complete plan means this is not a successful run.

## 4. gate compatibility

- **Configuration / grade:** GPT topology and Sol recursive configurations / C.
- **Tasks:** Multiple saved cases; category summary.
- **Observed:** Some semantically plausible navigation outputs were blocked by engineering consistency checks.
- **Explanation:** Examples include negated lists such as do not grasp/lift/place being mistaken for affirmative manipulation, unrecognized navigation phrasing, or strict role/query matching.
- **Expected handling:** Separate genuine unsupported actions from a checker that cannot verify a valid paraphrase; preserve the failure artifact and do not claim an ungenerated plan is correct.
- **Limit:** These historical delivery failures are documented, not silently repaired or counted as successful in this release.

## 5. justified ambiguity

- **Configuration / grade:** Historical Flash arms / A stop.
- **Tasks:** H08.
- **Observed:** Multiple scene candidates, such as racks 163 and 164, remained consistent with the instruction and available context.
- **Explanation:** The evidence did not uniquely identify a target.
- **Expected handling:** Ask a concrete distinguishing question and stop before a plan for an arbitrary candidate is executed.
- **Limit:** A justified stop counts as correct handling, not a completed navigation plan. Clarification is not automatically an error.

## 6. missing preservation structure

- **Configuration / grade:** Several complete historical plans / B.
- **Tasks:** Multiple saved cases; category summary.
- **Observed:** Navigation targets, order and terminal destination were correct, but no-moving/no-touching constraints were incomplete in the structured plan.
- **Explanation:** Natural-language restrictions were not fully preserved in machine-readable process constraints.
- **Expected handling:** Retain the restriction through the planning/export contract; use explicit review for any downstream change to constraint handling.
- **Limit:** Accepted only in the agreed relaxed symbolic-navigation metric; physical safety or controller compliance was not established.

Source hashes and the grading definition are in [historical_results.json](historical_results.json) and [results.md](results.md).
