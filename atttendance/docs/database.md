# Data model

- `users`: authentication identity and role; one-to-one profile relationship.
- `students`: school identifier, user and optional linked parent.
- `teachers`: employee identifier and user; classes point to assigned teacher.
- `parents`: user and linked children.
- `parent_consents`: current guardian consent for an individual student's biometric profile. Revoking consent deletes the profile.
- `classes`: class name/section and optional assigned teacher.
- `enrollments`: unique student-to-class enrollment.
- `face_profiles`: one encrypted embedding per student, model identifier and enrollment timestamp. The key is external configuration, not stored in the database.
- `attendance_sessions`: class, teacher, start/end timestamps, date and open state.
- `attendance`: unique session/student record, status, source and recorded time.
- `audit_logs`: actor, action, target and minimal non-biometric details.
- `school_settings`: key/value school configuration.

The initial Alembic revision creates the current metadata. Apply with `alembic upgrade head`. Production schema evolution should use reviewed incremental revisions; never edit a migration already applied in a deployed environment.

