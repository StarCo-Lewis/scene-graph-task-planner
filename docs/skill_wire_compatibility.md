# Skill catalogue versus action-wire support

The release includes the latest local formal catalogue, v0.3.2 with 30 entries, and the 48-item requirement mapping. These are different from the set that the inherited action-matrix protocol can encode.

The legacy matrix has two argument slots. Non-object parameters must be optional finite numeric defaults, and profile enrichment requires an existing semantic profile. A static audit finds 12 of the 30 entries satisfy these wire/profile requirements; 18 do not. This is a parameter-encoding assessment, not proof of planning accuracy or physical execution. Deeper validation can still reject a proposed plan.

| Skill ID | Wire/profile assessment | Reason |
|---|---|---|
| navigate_to_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| navigate_to_location | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| adjust_arm_height | Not offered under inherited matrix | unsupported_parameter_height; missing_semantic_profile |
| grasp_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| lift_held_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| place_object_on_surface | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| place_object_in_container | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| release_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| open_articulated_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| close_articulated_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| push_object | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_direction; unsupported_parameter_distance_m |
| pull_object | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_direction; unsupported_parameter_distance_m |
| press_control | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| pour_into_container | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_content |
| handover_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| walk_omnidirectional_velocity | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_vx_m_s; unsupported_parameter_vy_m_s; unsupported_parameter_yaw_rate_rad_s; unsupported_parameter_duration_s; unsupported_parameter_reference_frame |
| turn_by_stepping | Not offered under inherited matrix | unsupported_parameter_yaw_delta_rad; unsupported_parameter_reference_frame |
| turn_in_place | Not offered under inherited matrix | unsupported_parameter_yaw_delta_rad; unsupported_parameter_reference_frame |
| bend_and_lift_object | Encodable under inherited matrix | Two-slot/profile checks satisfied |
| reach_and_grasp_object | Not offered under inherited matrix | unsupported_parameter_hand |
| reach_and_place_object | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_target_pose; unsupported_parameter_hand |
| reset_lower_body | Not offered under inherited matrix | unsupported_parameter_reference_state |
| reset_upper_body | Not offered under inherited matrix | unsupported_parameter_reference_state |
| reset_manipulation_state | Not offered under inherited matrix | unsupported_parameter_component; unsupported_parameter_reference_state |
| place_object_at_pose | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_target_pose |
| hold_and_stabilize_object | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_hand; unsupported_parameter_constraint_reference; unsupported_parameter_duration_s |
| translate_with_contact_constraint | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_trajectory; unsupported_parameter_constraints; unsupported_parameter_hand |
| rotate_with_contact_constraint | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_trajectory; unsupported_parameter_constraints; unsupported_parameter_hand |
| follow_surface_contact | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_trajectory; unsupported_parameter_constraints; unsupported_parameter_hand |
| wipe_surface | Not offered under inherited matrix | more_than_two_slots; unsupported_parameter_region; unsupported_parameter_constraints; missing_semantic_profile |

In particular, wipe_surface is present in the updated catalogue but its four-parameter contract cannot be expressed by this inherited matrix. The release must report this boundary rather than rename a simpler action and pretend it can wipe. Updating the wire protocol to support richer parameters is separate work; the original pipeline is preserved.
