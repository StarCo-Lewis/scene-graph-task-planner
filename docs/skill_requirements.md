# Latest skill requirements and symbolic coverage

The latest meeting list contains 48 requirements, distinct from the 30 formal entries in the v0.3.2 symbolic catalogue. A definition is not a deployed expert. Partial mappings are components, not equivalent completed skills. No row is marked physically verified.

- **definition**: A symbolic definition exists; not proof of a deployed expert.
- **partial**: Some symbolic components exist; the complete requirement is not covered.
- **missing**: No sufficient existing skill contract is mapped.
- **component**: Historical planning/state/navigation components exist; not an executable skill contract in this release.

| Requirement | English name | Symbolic coverage | Existing skill IDs | Remaining boundary |
|---|---|---|---|---|
| O01 | Target grasp | partial | grasp_object, lift_held_object | The legacy contract separates grasp and lift; confirm a combined off-surface terminal condition and avoid lifting twice. |
| O02 | Placement and dropping | definition | place_object_on_surface, place_object_in_container, place_object_at_pose, release_object | Several placement/release contracts cover parts of this requirement; define stable placement, release, retreat and the held-object state on failure. |
| O03 | Push one light object | partial | push_object | Generic pushing needs light-object mass, friction, target-region and hand-retreat contracts. |
| O04 | Pull a light object by a handle | partial | pull_object, grasp_object | Generic pulling does not include obtaining the handle, pulling into the region and releasing as one verified operation. |
| O05 | Press a button | definition | press_control | Requires button localization, force/travel limits, trigger feedback and hand retreat. |
| O06 | Toggle a switch | partial | rotate_with_contact_constraint, translate_with_contact_constraint | Contact translation/rotation are components; switch positions, limits and switching effects require a dedicated contract. |
| O07 | Adjust a knob | partial | rotate_with_contact_constraint | Generic rotation does not establish knob operation; axis, scale, range and torque limits are required. |
| O08 | Open or close a hinged door | definition | open_articulated_object, close_articulated_object | Specify hinge/handle, opening extent, latch and closure criteria; an openable scene label is not measured operability. |
| O09 | Open or close a drawer | definition | open_articulated_object, close_articulated_object | Specify drawer axis, extent and closure criteria; a hinge trajectory is not interchangeable. |
| O10 | Remove or replace a light lid | partial | grasp_object, lift_held_object, place_object_on_surface, place_object_at_pose | Reuse pickup/placement components; lid-container pairing, alignment and the non-threaded scope remain to be defined. |
| O11 | Unscrew or tighten a lid | partial | rotate_with_contact_constraint | Threaded motion, stabilizing the second object, lid removal and tightening terminal conditions lack a complete contract. |
| O12 | Gather scattered light objects | missing | None | Continuous sweeping, collection region, object set and completion criteria are missing. |
| O13 | Spread a cloth | missing | None | wipe_surface assumes an already held cloth and does not unfold it; manual preparation must be an explicit input premise. |
| O14 | Transfer between hands | missing | None | External-recipient handover is not an inter-hand transfer; two-hand occupancy and release need a contract. |
| O15 | Pour with one hand | definition | pour_into_container | Specify source/recipient containers, contents, amount, upright recovery and spill handling. |
| O16 | Wipe a surface region | definition | wipe_surface | Assumes an already held cloth; specify region/path and contact settings. Traversing a surface does not prove cleanliness. |
| O17 | Align and stack | partial | place_object_on_surface, place_object_at_pose | Placement exists but stack alignment, stability and level-specific terminal state need a contract. |
| O18 | Insert for storage | partial | translate_with_contact_constraint, place_object_at_pose | Generic contact translation is not slot insertion; slot direction, clearance, contact/withdrawal and stable insertion criteria are required. |
| O19 | Stabilize and pour with two hands | partial | hold_and_stabilize_object, pour_into_container | Components exist but not a verified bimanual policy; resource sharing and coordinated execution cannot be inferred from a serial plan. |
| O20 | Reset hand posture | partial | reset_manipulation_state | The generic reset does not fix the hand/gripper/arm scope; this requirement concerns fingers/wrist after empty-hand or stable-placement confirmation. |
| M01 | Omnidirectional walking | definition | walk_omnidirectional_velocity | Requires velocity frame, duration/heartbeat and explicit stopping confirmation. |
| M02 | Stepping turn | definition | turn_by_stepping | Confirm angle units, error, translation drift and a stable final stance. |
| M03 | Turn in place | definition | turn_in_place | Clarify the distinction from a stepping turn and the permitted translation drift. |
| M04 | Whole-body reach and grasp | partial | reach_and_grasp_object | The reach/grasp contract does not prove body coordination, operating range or the off-surface terminal state. |
| M05 | Whole-body reach and place | partial | reach_and_place_object | The reach/place contract does not establish a crouch, body lean or return to standing. |
| M06 | Bend and lift a box | definition | bend_and_lift_object | The symbolic lift/stand goal exists; actual mass, size, two-arm control and stable-stance contracts are external. |
| M07 | Reset lower body | definition | reset_lower_body | Requires a standing reference state and held-object/support restrictions. |
| M08 | Reset upper body | definition | reset_upper_body | Distinguish empty-hand and carrying recovery; do not assume object release. |
| M09 | Controlled stop and stable stance | partial | walk_omnidirectional_velocity | Zero commanded velocity does not prove a stop; explicit stop, acknowledgment and stable-stance feedback are needed. |
| M10 | Fine adjustment of manipulation stance | partial | navigate_to_location, turn_in_place | Movement components do not establish fine stance adjustment; define frame, bounded displacement, accuracy and stable termination. |
| M11 | Hold a low working posture | missing | None | Arm-height adjustment is not whole-body lowering and posture holding. |
| M12 | Lower a box with both arms | partial | reach_and_place_object, place_object_at_pose | Single-hand/generic placement does not cover lowering a held box, two-arm disengagement and standing recovery. |
| M13 | Walk carrying an object in one hand | partial | hold_and_stabilize_object, navigate_to_location | Holding plus navigation does not prove a carrying gait; requires an explicit mode and stability boundary. |
| M14 | Walk carrying a box with both arms | missing | None | Lifting a box plus ordinary walking does not establish locomotion with visual occlusion. |
| M15 | Coordinate pedal opening and dropping | missing | None | Requires coordinated pedal press/hold/drop/release and well-defined lid/held-object state on interruption. |
| M16 | Push a box with whole-body coordination | partial | push_object | Tabletop pushing is not whole-body box pushing; contact, balance, load and disengagement contracts are needed. |
| M17 | Pull a box with whole-body coordination | partial | pull_object | Requires handle acquisition, backward stepping, whole-body balance and release. |
| M18 | Whole-body supported manipulation | partial | hold_and_stabilize_object | Holding does not establish body support while the other hand works; concurrent resource contracts are needed. |
| M19 | Recover balance after a small disturbance | missing | None | Specify disturbance limits, recovery and stop criteria; excludes standing up after a fall. |
| M20 | Follow a reference path and avoid obstacles | component | None | External navigation materials existed historically; the actual shared-framework path-following provider was not confirmed and is excluded here. |
| N01 | Mapping and exploration | component | None | Mapping produces state/resources; active exploration also needs planning/execution. Archived material is not an integrated mapping service. |
| N02 | Instruction-grounded navigation | definition | navigate_to_object, navigate_to_location | Binding/navigation planning definitions exist; execution requires a verified frame/map version and carrying mode. Do not send an object center as a ready-to-execute goal. |
| N03 | Search navigation | component | None | Query/route/observation components do not establish a complete search loop; budget, not-found and duplicate-target contracts are needed. |
| N04 | Ordered multi-stop scheduling | component | None | Room-recursive visit/memory components existed historically; resumption needs verified execution effects in the shared framework. |
| N05 | Select a manipulation stance | component | None | Historical geometric candidate-point filtering did not prove target orientation, manipulator reach or whole-body clearance; it is excluded from this release. |
| N06 | Replan after obstruction | component | None | Requires original obstruction feedback, map changes and evidence that an obstacle may be moved; this is not a replacement for local avoidance. |
| N07 | Acquire an additional observation | component | None | Observation querying may be reused; viewpoint changes need an actual provider. An image alone does not establish an action effect. |
| N08 | Inspection patrol | component | None | Visit/logging components do not establish patrol normal/abnormal/unknown criteria or a verification interface. |

[Machine-readable mapping](skill_requirements_mapping.json). The English text translates the supplied meeting requirement list and the November 14 local mapping. It does not grant execution permissions or establish physical preconditions.
