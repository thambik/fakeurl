from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Attendance, AttendanceSession, AttendanceStatus, AuditLog, Enrollment, Parent, ParentConsent, SchoolClass, Student, Teacher, User


def audit(db: Session, actor: User | None, action: str, target_type: str, target_id: int, details: str | None = None) -> None:
    db.add(AuditLog(actor_user_id=actor.id if actor else None, action=action, target_type=target_type, target_id=str(target_id), details=details))


def teacher_for_user(db: Session, user: User) -> Teacher:
    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))
    if teacher is None:
        raise HTTPException(status_code=403, detail="Teacher profile is unavailable")
    return teacher


def get_session_for_teacher(db: Session, session_id: int, user: User) -> AttendanceSession:
    session = db.get(AttendanceSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    if user.role == "teacher" and session.teacher_id != teacher_for_user(db, user).id:
        raise HTTPException(status_code=403, detail="This session is not assigned to you")
    return session


def mark_attendance(db: Session, session: AttendanceSession, student_id: int, status: str, source: str) -> Attendance:
    if not session.is_open:
        raise HTTPException(status_code=409, detail="Attendance session is closed")
    enrolled = db.scalar(select(Enrollment).where(Enrollment.student_id == student_id, Enrollment.class_id == session.class_id))
    if enrolled is None:
        raise HTTPException(status_code=400, detail="Student is not enrolled in this class")
    record = db.scalar(select(Attendance).where(Attendance.session_id == session.id, Attendance.student_id == student_id))
    if record:
        if source != "manual":
            return record
        record.status = status
        record.source = "manual"
        record.recorded_at = datetime.now(timezone.utc)
        return record
    record = Attendance(session_id=session.id, student_id=student_id, status=status, source=source)
    db.add(record)
    db.flush()
    return record


def close_session(db: Session, session: AttendanceSession) -> None:
    if not session.is_open:
        raise HTTPException(status_code=409, detail="Attendance session is already closed")
    students = db.scalars(select(Enrollment.student_id).where(Enrollment.class_id == session.class_id)).all()
    for student_id in students:
        existing = db.scalar(select(Attendance).where(Attendance.session_id == session.id, Attendance.student_id == student_id))
        if existing is None:
            db.add(Attendance(session_id=session.id, student_id=student_id, status=AttendanceStatus.ABSENT.value, source="session_close"))
    session.is_open = False
    session.ended_at = datetime.now(timezone.utc)


def report_rows(db: Session, class_id: int | None = None, start_date: date | None = None, end_date: date | None = None):
    query = select(AttendanceSession).where(AttendanceSession.is_open.is_(False))
    if class_id is not None:
        query = query.where(AttendanceSession.class_id == class_id)
    if start_date:
        query = query.where(AttendanceSession.session_date >= start_date)
    if end_date:
        query = query.where(AttendanceSession.session_date <= end_date)
    sessions = db.scalars(query).all()
    counts: dict[int, list[int]] = {}
    for session in sessions:
        rows = db.scalars(select(Attendance).where(Attendance.session_id == session.id)).all()
        for row in rows:
            values = counts.setdefault(row.student_id, [0, 0, 0])
            values[2] += 1
            if row.status in (AttendanceStatus.PRESENT.value, AttendanceStatus.LATE.value):
                values[0] += 1
            elif row.status == AttendanceStatus.ABSENT.value:
                values[1] += 1
    student_ids = list(counts)
    if not student_ids:
        return []
    students = db.scalars(select(Student).options(joinedload(Student.user)).where(Student.id.in_(student_ids))).all()
    result = []
    for student in students:
        present, absent, total = counts[student.id]
        result.append({
            "student_id": student.id,
            "student_number": student.student_number,
            "student_name": student.user.full_name,
            "present": present,
            "absent": absent,
            "total_sessions": total,
            "percentage": round(100 * present / total, 2) if total else 0.0,
        })
    return sorted(result, key=lambda row: row["student_name"].casefold())

