from io import BytesIO

import jwt
import pytest
from fastapi import HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import AttendanceSession, Enrollment, FaceProfile, Parent, ParentConsent, SchoolClass, Student, Teacher, User
from app.routes.api import (
    attendance_report,
    create_class,
    create_parent,
    create_student,
    create_teacher,
    enroll_face,
    get_session_attendance,
    list_students,
    login,
    manual_attendance,
    parent_consent,
    start_session,
    stop_session,
    update_class,
    update_student,
)
from app.schemas import ClassCreate, ClassUpdate, ConsentIn, LoginIn, ManualAttendanceIn, ParentCreate, SessionCreate, StudentCreate, StudentUpdate, TeacherCreate
from app.security import create_access_token, hash_password, require_roles, verify_password, current_user


def test_login_token_and_role_authorization(db, admin_user):
    response = login(LoginIn(email=admin_user.email, password="very-secure-password"), db)
    assert response["user"].role == "admin"
    payload = jwt.decode(response["access_token"], settings.jwt_secret_key, algorithms=["HS256"])
    assert payload["sub"] == str(admin_user.id)
    assert verify_password("very-secure-password", admin_user.password_hash)
    authenticated = current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=response["access_token"]), db)
    assert authenticated.id == admin_user.id

    teacher = User(email="teacher@example.com", full_name="Teacher", password_hash="unused", role="teacher")
    with pytest.raises(HTTPException) as denied:
        require_roles("admin")(user=teacher)
    assert denied.value.status_code == 403


def test_student_teacher_and_class_operations(db, admin_user):
    teacher = create_teacher(TeacherCreate(
        email="teacher@example.com", full_name="T. Teacher", password="long-password-123", employee_code="T-1",
    ), db, admin_user)
    school_class = create_class(ClassCreate(name="Grade 4", section="A", teacher_id=teacher["id"]), db, admin_user)
    parent = create_parent(ParentCreate(
        email="parent@example.com", full_name="P. Guardian", password="long-password-456",
    ), db, admin_user)
    student = create_student(StudentCreate(
        email="student@example.com", full_name="S. Learner", password="long-password-789",
        student_number="S-1", class_id=school_class.id, parent_id=parent["id"],
    ), db, admin_user)
    assert student["student_number"] == "S-1"
    assert list_students(db, admin_user)[0]["full_name"] == "S. Learner"

    updated = update_student(student["id"], StudentUpdate(full_name="Updated Learner"), db, admin_user)
    assert updated["full_name"] == "Updated Learner"
    changed_class = update_class(school_class.id, ClassUpdate(section="B"), db, admin_user)
    assert changed_class.section == "B"


def test_attendance_duplicate_prevention_absence_and_csv_report(db, admin_user):
    teacher_user = User(
        email="staff@example.com", full_name="Staff", password_hash=hash_password("long-password-123"), role="teacher",
    )
    db.add(teacher_user)
    db.flush()
    teacher = Teacher(user_id=teacher_user.id, employee_code="T-2")
    db.add(teacher)
    db.flush()
    school_class = SchoolClass(name="Grade 5", section="B", teacher_id=teacher.id)
    db.add(school_class)
    db.flush()
    students = []
    for number in ("S-2", "S-3"):
        account = User(email=f"{number}@example.test", full_name=number, password_hash="x", role="student")
        db.add(account)
        db.flush()
        student = Student(user_id=account.id, student_number=number)
        db.add(student)
        db.flush()
        db.add(Enrollment(student_id=student.id, class_id=school_class.id))
        students.append(student)
    db.commit()

    session = start_session(SessionCreate(class_id=school_class.id), db, teacher_user)
    payload = ManualAttendanceIn(student_id=students[0].id, status="present")
    first = manual_attendance(session["id"], payload, db, teacher_user)
    duplicate = manual_attendance(session["id"], payload, db, teacher_user)
    assert first.id == duplicate.id
    assert db.scalar(select(AttendanceSession.id).where(AttendanceSession.id == session["id"]))

    stop_session(session["id"], db, teacher_user)
    records = get_session_attendance(session["id"], db, teacher_user)
    assert sorted(row["status"] for row in records) == ["absent", "present"]
    report = attendance_report(school_class.id, None, None, db, teacher_user)
    assert report.status_code == 200
    text = report.body.decode()
    assert "student_number,student_name,present,absent,total_sessions,percentage" in text
    assert ",1,0,1,100.0" in text
    own_reports = attendance_report(None, None, None, db, teacher_user).body.decode()
    assert "S-2" in own_reports
    other_user = User(email="other-staff@example.com", full_name="Other", password_hash="x", role="teacher")
    db.add(other_user)
    db.flush()
    other_teacher = Teacher(user_id=other_user.id, employee_code="T-3")
    db.add(other_teacher)
    db.flush()
    unrelated_class = SchoolClass(name="Grade 6", section="C", teacher_id=other_teacher.id)
    db.add(unrelated_class)
    db.commit()
    with pytest.raises(HTTPException) as denied:
        attendance_report(unrelated_class.id, None, None, db, teacher_user)
    assert denied.value.status_code == 403


def test_parent_consent_revocation_deletes_biometric_profile(db):
    parent_user = User(
        email="guardian@example.com", full_name="Guardian", password_hash="unused", role="parent",
    )
    student_user = User(email="child@example.com", full_name="Child", password_hash="unused", role="student")
    db.add_all([parent_user, student_user])
    db.flush()
    parent = Parent(user_id=parent_user.id)
    db.add(parent)
    db.flush()
    student = Student(user_id=student_user.id, student_number="C-1", parent_id=parent.id)
    db.add(student)
    db.flush()
    db.add(ParentConsent(student_id=student.id, parent_id=parent.id))
    db.add(FaceProfile(student_id=student.id, encrypted_embedding=b"encrypted-test-data", model_name="test"))
    db.commit()

    assert parent_consent(student.id, ConsentIn(consent=False), db, parent_user) == {"consent": False}
    assert db.scalar(select(ParentConsent).where(ParentConsent.student_id == student.id)) is None
    assert db.scalar(select(FaceProfile).where(FaceProfile.student_id == student.id)) is None


def test_face_enrollment_rejects_missing_student_without_invoking_model(db, admin_user):
    coroutine = enroll_face(999, UploadFile(file=BytesIO(b"unused"), filename="face.jpg"), db, admin_user)
    with pytest.raises(HTTPException) as missing:
        coroutine.send(None)
    assert missing.value.status_code == 404


def test_openapi_exposes_protected_api_routes():
    schema = app.openapi()
    assert "/api/v1/attendance/sessions/{session_id}/recognize" in schema["paths"]
    assert "/api/v1/students/{student_id}/face-profile" in schema["paths"]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]

