import csv
import importlib
import io
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.ai.face_engine import FaceInputError, FaceModelUnavailable, decrypt_embedding, encrypt_embedding, extract_embedding, match_embedding
from app.config import settings
from app.database import get_db
from app.models import Attendance, AttendanceSession, AttendanceStatus, AuditLog
from app.models import Enrollment, FaceProfile, Parent, ParentConsent, SchoolClass, SchoolSetting, Student, Teacher, User
from app.schemas import ClassCreate, ClassOut, ClassUpdate, ConsentIn, LoginIn, ManualAttendanceIn, ParentCreate, RecognitionOut, SessionCreate, StudentCreate, StudentUpdate, TeacherCreate, TeacherUpdate, UserOut
from app.security import create_access_token, hash_password, require_roles, verify_password, current_user
from app.services import audit, close_session, get_session_for_teacher, mark_attendance, report_rows, teacher_for_user


router = APIRouter()
MAX_FACE_IMAGE_BYTES = 5 * 1024 * 1024


async def read_face_image(image: UploadFile) -> bytes:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Upload must be an image.")
    content = await image.read(MAX_FACE_IMAGE_BYTES + 1)
    if len(content) > MAX_FACE_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 5 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="Image is empty.")
    return content


def verify_liveness(image_bytes: bytes) -> bool:
    if not settings.liveness_provider:
        return False
    try:
        module_name, function_name = settings.liveness_provider.split(":", maxsplit=1)
        checker = getattr(importlib.import_module(module_name), function_name)
        return checker(image_bytes) is True
    except (ImportError, AttributeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Configured liveness provider could not be loaded.") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Configured liveness provider failed; attendance was not marked.") from exc


@router.post("/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash) or not user.is_active:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": UserOut.model_validate(user)}


@router.post("/auth/logout")
def logout(_: User = Depends(current_user)):
    return {"message": "Logged out. Discard the access token on this device."}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/students")
def list_students(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Student).options(joinedload(Student.user), joinedload(Student.enrollment).joinedload(Enrollment.school_class))
    if user.role == "student":
        query = query.where(Student.user_id == user.id)
    elif user.role == "parent":
        parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
        query = query.where(Student.parent_id == (parent.id if parent else -1))
    elif user.role == "teacher":
        teacher = teacher_for_user(db, user)
        class_ids = select(SchoolClass.id).where(SchoolClass.teacher_id == teacher.id)
        query = query.where(Student.id.in_(select(Enrollment.student_id).where(Enrollment.class_id.in_(class_ids))))
    students = db.scalars(query.order_by(Student.student_number)).unique().all()
    return [{
        "id": item.id, "student_number": item.student_number, "full_name": item.user.full_name,
        "email": item.user.email, "class_id": item.enrollment.class_id if item.enrollment else None,
        "class_name": f"{item.enrollment.school_class.name} {item.enrollment.school_class.section}" if item.enrollment else None,
    } for item in students]


@router.post("/students", status_code=201)
def create_student(payload: StudentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(User.id).where(func.lower(User.email) == payload.email.lower())):
        raise HTTPException(status_code=409, detail="Email is already registered")
    if db.scalar(select(Student.id).where(Student.student_number == payload.student_number)):
        raise HTTPException(status_code=409, detail="Student number is already registered")
    school_class = db.get(SchoolClass, payload.class_id)
    if school_class is None:
        raise HTTPException(status_code=404, detail="Class not found")
    if payload.parent_id is not None and db.get(Parent, payload.parent_id) is None:
        raise HTTPException(status_code=404, detail="Parent not found")
    account = User(email=payload.email.lower(), full_name=payload.full_name, password_hash=hash_password(payload.password), role="student")
    db.add(account)
    db.flush()
    student = Student(user_id=account.id, student_number=payload.student_number, parent_id=payload.parent_id)
    db.add(student)
    db.flush()
    db.add(Enrollment(student_id=student.id, class_id=payload.class_id))
    audit(db, user, "student.create", "student", student.id)
    db.commit()
    return {"id": student.id, "student_number": student.student_number, "full_name": account.full_name}


@router.patch("/students/{student_id}")
def update_student(student_id: int, payload: StudentUpdate, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    student = db.scalar(select(Student).options(joinedload(Student.user)).where(Student.id == student_id))
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    changes = payload.model_dump(exclude_unset=True)
    if any(changes.get(key) is None for key in ("email", "full_name", "student_number") if key in changes):
        raise HTTPException(status_code=422, detail="Email, name, and student number cannot be null")
    if "parent_id" in changes and changes["parent_id"] is not None and db.get(Parent, changes["parent_id"]) is None:
        raise HTTPException(status_code=404, detail="Parent not found")
    if "parent_id" in changes and changes["parent_id"] != student.parent_id:
        old_consent = db.scalar(select(ParentConsent).where(ParentConsent.student_id == student_id))
        if old_consent:
            db.delete(old_consent)
        if student.face_profile:
            db.delete(student.face_profile)
    if "class_id" in changes:
        class_id = changes.pop("class_id")
        if class_id is not None and db.get(SchoolClass, class_id) is None:
            raise HTTPException(status_code=404, detail="Class not found")
        if class_id is not None:
            enrollment = db.scalar(select(Enrollment).where(Enrollment.student_id == student_id))
            if enrollment is None:
                db.add(Enrollment(student_id=student_id, class_id=class_id))
            else:
                enrollment.class_id = class_id
        else:
            enrollment = db.scalar(select(Enrollment).where(Enrollment.student_id == student_id))
            if enrollment is not None:
                db.delete(enrollment)
    if "email" in changes and changes["email"] is not None:
        duplicate = db.scalar(select(User.id).where(func.lower(User.email) == changes["email"].lower(), User.id != student.user_id))
        if duplicate:
            raise HTTPException(status_code=409, detail="Email is already registered")
        changes["email"] = changes["email"].lower()
    if "student_number" in changes and db.scalar(select(Student.id).where(
        Student.student_number == changes["student_number"], Student.id != student_id
    )):
        raise HTTPException(status_code=409, detail="Student number is already registered")
    for key in ("email", "full_name"):
        if key in changes:
            setattr(student.user, key, changes.pop(key))
    for key, value in changes.items():
        setattr(student, key, value)
    audit(db, actor, "student.update", "student", student_id)
    db.commit()
    return {"id": student.id, "student_number": student.student_number, "full_name": student.user.full_name}


@router.delete("/students/{student_id}", status_code=204)
def delete_student(student_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    audit(db, user, "student.delete", "student", student.id)
    account = student.user
    db.delete(student)
    db.flush()
    db.delete(account)
    db.commit()
    return Response(status_code=204)


@router.get("/teachers")
def list_teachers(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    teachers = db.scalars(select(Teacher).options(joinedload(Teacher.user)).order_by(Teacher.employee_code)).unique().all()
    return [{"id": t.id, "employee_code": t.employee_code, "full_name": t.user.full_name, "email": t.user.email} for t in teachers]


@router.post("/teachers", status_code=201)
def create_teacher(payload: TeacherCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(User.id).where(func.lower(User.email) == payload.email.lower())):
        raise HTTPException(status_code=409, detail="Email is already registered")
    if db.scalar(select(Teacher.id).where(Teacher.employee_code == payload.employee_code)):
        raise HTTPException(status_code=409, detail="Employee code is already registered")
    account = User(email=payload.email.lower(), full_name=payload.full_name, password_hash=hash_password(payload.password), role="teacher")
    db.add(account)
    db.flush()
    teacher = Teacher(user_id=account.id, employee_code=payload.employee_code)
    db.add(teacher)
    db.flush()
    audit(db, user, "teacher.create", "teacher", teacher.id)
    db.commit()
    return {"id": teacher.id, "employee_code": teacher.employee_code, "full_name": account.full_name}


@router.patch("/teachers/{teacher_id}")
def update_teacher(teacher_id: int, payload: TeacherUpdate, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    teacher = db.scalar(select(Teacher).options(joinedload(Teacher.user)).where(Teacher.id == teacher_id))
    if teacher is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    changes = payload.model_dump(exclude_unset=True)
    if any(changes.get(key) is None for key in ("email", "full_name", "employee_code") if key in changes):
        raise HTTPException(status_code=422, detail="Email, name, and employee code cannot be null")
    if "email" in changes and changes["email"] is not None:
        duplicate = db.scalar(select(User.id).where(func.lower(User.email) == changes["email"].lower(), User.id != teacher.user_id))
        if duplicate:
            raise HTTPException(status_code=409, detail="Email is already registered")
        teacher.user.email = changes.pop("email").lower()
    if "full_name" in changes:
        teacher.user.full_name = changes.pop("full_name")
    if "employee_code" in changes and db.scalar(select(Teacher.id).where(
        Teacher.employee_code == changes["employee_code"], Teacher.id != teacher_id
    )):
        raise HTTPException(status_code=409, detail="Employee code is already registered")
    for key, value in changes.items():
        setattr(teacher, key, value)
    audit(db, actor, "teacher.update", "teacher", teacher_id)
    db.commit()
    return {"id": teacher.id, "employee_code": teacher.employee_code, "full_name": teacher.user.full_name}


@router.delete("/teachers/{teacher_id}", status_code=204)
def delete_teacher(teacher_id: int, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    teacher = db.scalar(select(Teacher).options(joinedload(Teacher.user)).where(Teacher.id == teacher_id))
    if teacher is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    if db.scalar(select(AttendanceSession.id).where(AttendanceSession.teacher_id == teacher_id).limit(1)):
        raise HTTPException(status_code=409, detail="Teacher has attendance history and cannot be deleted")
    audit(db, actor, "teacher.delete", "teacher", teacher_id)
    account = teacher.user
    db.delete(teacher)
    db.flush()
    db.delete(account)
    db.commit()
    return Response(status_code=204)


@router.get("/classes", response_model=list[ClassOut])
def list_classes(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(SchoolClass).order_by(SchoolClass.name, SchoolClass.section)
    if user.role == "teacher":
        teacher = teacher_for_user(db, user)
        query = query.where(SchoolClass.teacher_id == teacher.id)
    elif user.role == "student":
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        if student is None:
            return []
        query = query.where(SchoolClass.id == select(Enrollment.class_id).where(Enrollment.student_id == student.id).scalar_subquery())
    elif user.role == "parent":
        parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
        child_ids = select(Student.id).where(Student.parent_id == (parent.id if parent else -1))
        query = query.where(SchoolClass.id.in_(select(Enrollment.class_id).where(Enrollment.student_id.in_(child_ids))))
    return db.scalars(query).all()


@router.post("/classes", status_code=201, response_model=ClassOut)
def create_class(payload: ClassCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if payload.teacher_id is not None and db.get(Teacher, payload.teacher_id) is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    if db.scalar(select(SchoolClass.id).where(SchoolClass.name == payload.name, SchoolClass.section == payload.section)):
        raise HTTPException(status_code=409, detail="Class and section already exist")
    school_class = SchoolClass(name=payload.name, section=payload.section, teacher_id=payload.teacher_id)
    db.add(school_class)
    db.flush()
    audit(db, user, "class.create", "class", school_class.id)
    db.commit()
    return school_class


@router.patch("/classes/{class_id}", response_model=ClassOut)
def update_class(class_id: int, payload: ClassUpdate, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    school_class = db.get(SchoolClass, class_id)
    if school_class is None:
        raise HTTPException(status_code=404, detail="Class not found")
    changes = payload.model_dump(exclude_unset=True)
    if any(changes.get(key) is None for key in ("name", "section") if key in changes):
        raise HTTPException(status_code=422, detail="Class name and section cannot be null")
    teacher_id = changes.get("teacher_id")
    if teacher_id is not None and db.get(Teacher, teacher_id) is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    new_name = changes.get("name", school_class.name)
    new_section = changes.get("section", school_class.section)
    duplicate = db.scalar(select(SchoolClass.id).where(
        SchoolClass.name == new_name, SchoolClass.section == new_section, SchoolClass.id != class_id
    ))
    if duplicate:
        raise HTTPException(status_code=409, detail="Class and section already exist")
    for key, value in changes.items():
        setattr(school_class, key, value)
    audit(db, actor, "class.update", "class", class_id)
    db.commit()
    return school_class


@router.delete("/classes/{class_id}", status_code=204)
def delete_class(class_id: int, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    school_class = db.get(SchoolClass, class_id)
    if school_class is None:
        raise HTTPException(status_code=404, detail="Class not found")
    if db.scalar(select(AttendanceSession.id).where(AttendanceSession.class_id == class_id).limit(1)):
        raise HTTPException(status_code=409, detail="Class has attendance history and cannot be deleted")
    audit(db, actor, "class.delete", "class", class_id)
    db.delete(school_class)
    db.commit()
    return Response(status_code=204)


@router.put("/classes/{class_id}/students/{student_id}")
def assign_student(class_id: int, student_id: int, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    if db.get(SchoolClass, class_id) is None or db.get(Student, student_id) is None:
        raise HTTPException(status_code=404, detail="Class or student not found")
    enrollment = db.scalar(select(Enrollment).where(Enrollment.student_id == student_id))
    if enrollment is None:
        enrollment = Enrollment(student_id=student_id, class_id=class_id)
        db.add(enrollment)
    else:
        enrollment.class_id = class_id
    audit(db, actor, "student.class_assign", "student", student_id, f"class_id={class_id}")
    db.commit()
    return {"student_id": student_id, "class_id": class_id}


@router.post("/parents", status_code=201)
def create_parent(payload: ParentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(User.id).where(func.lower(User.email) == payload.email.lower())):
        raise HTTPException(status_code=409, detail="Email is already registered")
    account = User(email=payload.email.lower(), full_name=payload.full_name, password_hash=hash_password(payload.password), role="parent")
    db.add(account)
    db.flush()
    parent = Parent(user_id=account.id, phone=payload.phone)
    db.add(parent)
    db.flush()
    audit(db, user, "parent.create", "parent", parent.id)
    db.commit()
    return {"id": parent.id, "full_name": account.full_name}


@router.get("/parents")
def list_parents(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    parents = db.scalars(select(Parent).options(joinedload(Parent.user)).order_by(Parent.id)).unique().all()
    return [{"id": p.id, "full_name": p.user.full_name, "email": p.user.email, "phone": p.phone} for p in parents]


@router.put("/parents/{parent_id}/children/{student_id}")
def link_child(parent_id: int, student_id: int, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    parent = db.get(Parent, parent_id)
    student = db.get(Student, student_id)
    if parent is None or student is None:
        raise HTTPException(status_code=404, detail="Parent or student not found")
    if student.parent_id != parent_id:
        consent = db.scalar(select(ParentConsent).where(ParentConsent.student_id == student_id))
        if consent:
            db.delete(consent)
        if student.face_profile:
            db.delete(student.face_profile)
        student.parent_id = parent_id
    audit(db, actor, "parent.child_link", "student", student_id, f"parent_id={parent_id}")
    db.commit()
    return {"parent_id": parent_id, "student_id": student_id}


@router.get("/parents/children")
def parent_children(db: Session = Depends(get_db), user: User = Depends(require_roles("parent"))):
    parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
    if parent is None:
        raise HTTPException(status_code=404, detail="Parent profile not found")
    students = db.scalars(select(Student).options(joinedload(Student.user)).where(Student.parent_id == parent.id)).unique().all()
    return [{"id": s.id, "student_number": s.student_number, "full_name": s.user.full_name} for s in students]


@router.post("/parents/children/{student_id}/consent")
def parent_consent(student_id: int, payload: ConsentIn, db: Session = Depends(get_db), user: User = Depends(require_roles("parent"))):
    parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
    student = db.get(Student, student_id)
    if parent is None or student is None or student.parent_id != parent.id:
        raise HTTPException(status_code=404, detail="Linked child not found")
    consent = db.scalar(select(ParentConsent).where(ParentConsent.student_id == student_id))
    if payload.consent:
        if consent is None:
            consent = ParentConsent(student_id=student_id, parent_id=parent.id)
            db.add(consent)
        else:
            consent.parent_id = parent.id
        audit(db, user, "biometric.consent.granted", "student", student_id)
    else:
        if consent:
            db.delete(consent)
        if student.face_profile:
            db.delete(student.face_profile)
        audit(db, user, "biometric.consent.revoked", "student", student_id)
    db.commit()
    return {"consent": payload.consent}


@router.post("/students/{student_id}/face-profile", status_code=201)
async def enroll_face(student_id: int, image: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "teacher"))):
    student = db.scalar(select(Student).options(joinedload(Student.user), joinedload(Student.enrollment)).where(Student.id == student_id))
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    if student.parent_id is None or db.scalar(select(ParentConsent.id).where(
        ParentConsent.student_id == student_id, ParentConsent.parent_id == student.parent_id
    )) is None:
        raise HTTPException(status_code=403, detail="Documented parent/guardian biometric consent is required")
    if user.role == "teacher":
        teacher = teacher_for_user(db, user)
        school_class = db.get(SchoolClass, student.enrollment.class_id) if student.enrollment else None
        if school_class is None or school_class.teacher_id != teacher.id:
            raise HTTPException(status_code=403, detail="Student is not in your assigned class")
    try:
        embedding = extract_embedding(await read_face_image(image))
        encrypted = encrypt_embedding(embedding)
    except FaceInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    profile = db.scalar(select(FaceProfile).where(FaceProfile.student_id == student_id))
    if profile is None:
        profile = FaceProfile(student_id=student_id, encrypted_embedding=encrypted, model_name=settings.insightface_model)
        db.add(profile)
    else:
        profile.encrypted_embedding = encrypted
        profile.model_name = settings.insightface_model
    audit(db, user, "face_profile.enroll", "student", student_id)
    db.commit()
    return {"status": "enrolled", "student_id": student_id, "model": settings.insightface_model}


@router.delete("/students/{student_id}/face-profile", status_code=204)
def delete_face_profile(student_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    profile = db.scalar(select(FaceProfile).where(FaceProfile.student_id == student_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Face profile not found")
    db.delete(profile)
    audit(db, user, "face_profile.delete", "student", student_id)
    db.commit()
    return Response(status_code=204)


@router.post("/attendance/sessions", status_code=201)
def start_session(payload: SessionCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "teacher"))):
    school_class = db.get(SchoolClass, payload.class_id)
    if school_class is None:
        raise HTTPException(status_code=404, detail="Class not found")
    teacher = teacher_for_user(db, user) if user.role == "teacher" else None
    if teacher and school_class.teacher_id != teacher.id:
        raise HTTPException(status_code=403, detail="Class is not assigned to you")
    session = AttendanceSession(class_id=school_class.id, teacher_id=teacher.id if teacher else school_class.teacher_id)
    if session.teacher_id is None:
        raise HTTPException(status_code=400, detail="Assign a teacher before starting an attendance session")
    db.add(session)
    db.flush()
    audit(db, user, "attendance_session.start", "attendance_session", session.id)
    db.commit()
    return {"id": session.id, "class_id": session.class_id, "is_open": session.is_open, "started_at": session.started_at}


@router.post("/attendance/sessions/{session_id}/recognize", response_model=RecognitionOut)
async def recognize(session_id: int, image: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "teacher"))):
    session = get_session_for_teacher(db, session_id, user)
    if not session.is_open:
        raise HTTPException(status_code=409, detail="Attendance session is closed")
    try:
        image_bytes = await read_face_image(image)
        query_embedding = extract_embedding(image_bytes)
    except FaceInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    profiles = db.execute(
        select(Student.id, User.full_name, FaceProfile.encrypted_embedding)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .join(User, User.id == Student.user_id)
        .join(FaceProfile, FaceProfile.student_id == Student.id)
        .join(ParentConsent, (ParentConsent.student_id == Student.id) & (ParentConsent.parent_id == Student.parent_id))
        .where(Enrollment.class_id == session.class_id)
    ).all()
    decoded = []
    try:
        decoded = [(student_id, name, decrypt_embedding(blob)) for student_id, name, blob in profiles]
    except FaceModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    match = match_embedding(query_embedding, decoded)
    if match is None:
        return RecognitionOut(matched=False, detail="Unknown face or below the configured similarity threshold.")
    student_id, student_name, score = match
    marked = False
    liveness_checked = False
    if settings.liveness_provider:
        liveness_checked = verify_liveness(image_bytes)
    if settings.auto_mark_recognized and liveness_checked:
        mark_attendance(db, session, student_id, AttendanceStatus.PRESENT.value, "face")
        audit(db, user, "attendance.face_mark", "student", student_id, f"session_id={session.id}")
        db.commit()
        marked = True
    return RecognitionOut(
        matched=True, student_id=student_id, student_name=student_name, similarity=round(score, 4),
        attendance_marked=marked, liveness_checked=liveness_checked,
        detail=("Recognized and marked present." if marked else
                "Recognized and liveness verified; confirm attendance manually." if liveness_checked else
                "Recognized, but liveness was not verified. Confirm attendance manually."),
    )


@router.post("/attendance/sessions/{session_id}/manual")
def manual_attendance(session_id: int, payload: ManualAttendanceIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "teacher"))):
    session = get_session_for_teacher(db, session_id, user)
    record = mark_attendance(db, session, payload.student_id, payload.status, "manual")
    audit(db, user, "attendance.manual_correction", "attendance", record.id, f"status={payload.status}")
    db.commit()
    db.refresh(record)
    return record


@router.post("/attendance/sessions/{session_id}/stop")
def stop_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "teacher"))):
    session = get_session_for_teacher(db, session_id, user)
    close_session(db, session)
    audit(db, user, "attendance_session.stop", "attendance_session", session.id)
    db.commit()
    return {"id": session.id, "is_open": False, "ended_at": session.ended_at}


@router.get("/attendance/sessions/{session_id}")
def get_session_attendance(session_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    session = db.get(AttendanceSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    if user.role == "teacher" and get_session_for_teacher(db, session_id, user).id != session.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if user.role == "student":
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        if not db.scalar(select(Enrollment.id).where(Enrollment.student_id == student.id, Enrollment.class_id == session.class_id)):
            raise HTTPException(status_code=403, detail="Forbidden")
    if user.role == "parent":
        parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
        if not db.scalar(select(Student.id).join(Enrollment).where(Student.parent_id == parent.id, Enrollment.class_id == session.class_id)):
            raise HTTPException(status_code=403, detail="Forbidden")
    records = db.scalars(select(Attendance).options(joinedload(Attendance.student).joinedload(Student.user)).where(Attendance.session_id == session.id)).unique().all()
    return [{"id": row.id, "student_id": row.student_id, "student_name": row.student.user.full_name, "status": row.status,
             "source": row.source, "recorded_at": row.recorded_at} for row in records]


@router.get("/attendance/history")
def attendance_history(student_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role == "student":
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        allowed_ids = [student.id] if student else []
    elif user.role == "parent":
        parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
        allowed_ids = list(db.scalars(select(Student.id).where(Student.parent_id == (parent.id if parent else -1))).all())
    elif user.role == "teacher":
        teacher = teacher_for_user(db, user)
        allowed_ids = list(db.scalars(select(Enrollment.student_id).join(SchoolClass).where(SchoolClass.teacher_id == teacher.id)).all())
    else:
        allowed_ids = None
    if student_id is not None:
        if allowed_ids is not None and student_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Forbidden")
        allowed_ids = [student_id]
    query = select(Attendance).options(joinedload(Attendance.session), joinedload(Attendance.student).joinedload(Student.user)).order_by(Attendance.recorded_at.desc())
    if allowed_ids is not None:
        query = query.where(Attendance.student_id.in_(allowed_ids))
    rows = db.scalars(query.limit(500)).unique().all()
    return [{"id": a.id, "student_id": a.student_id, "student_name": a.student.user.full_name, "session_id": a.session_id,
             "date": a.session.session_date, "status": a.status, "source": a.source, "recorded_at": a.recorded_at} for a in rows]


@router.get("/attendance/report.csv")
def attendance_report(class_id: int | None = None, start_date: date | None = None, end_date: date | None = None,
                      db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "teacher"))):
    if user.role == "teacher":
        teacher = teacher_for_user(db, user)
        assigned_class_ids = list(db.scalars(select(SchoolClass.id).where(SchoolClass.teacher_id == teacher.id)).all())
        if class_id is not None and class_id not in assigned_class_ids:
            raise HTTPException(status_code=403, detail="Class is not assigned to you")
        selected_class_ids = [class_id] if class_id is not None else assigned_class_ids
        rows = [row for assigned_id in selected_class_ids for row in report_rows(db, assigned_id, start_date, end_date)]
    else:
        rows = report_rows(db, class_id, start_date, end_date)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["student_id", "student_number", "student_name", "present", "absent", "total_sessions", "percentage"])
    writer.writeheader()
    writer.writerows(rows)
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=attendance-report.csv"})


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    today = date.today()
    if user.role == "admin":
        total_students = db.scalar(select(func.count(Student.id)) ) or 0
        total_teachers = db.scalar(select(func.count(Teacher.id))) or 0
        sessions = db.scalars(select(AttendanceSession).where(AttendanceSession.session_date == today)).all()
        session_ids = [s.id for s in sessions]
        present = db.scalar(select(func.count(Attendance.id)).where(Attendance.session_id.in_(session_ids), Attendance.status.in_(["present", "late"]))) or 0 if session_ids else 0
        total = sum(len(db.scalars(select(Enrollment.id).where(Enrollment.class_id == s.class_id)).all()) for s in sessions)
        return {"role": user.role, "students": total_students, "teachers": total_teachers, "present_today": present,
                "absent_today": max(total - present, 0), "attendance_percentage": round(present * 100 / total, 2) if total else 0}
    if user.role == "teacher":
        teacher = teacher_for_user(db, user)
        classes = db.scalars(select(SchoolClass).where(SchoolClass.teacher_id == teacher.id)).all()
        return {"role": user.role, "classes": [{"id": c.id, "name": c.name, "section": c.section} for c in classes]}
    if user.role == "student":
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        ids = [student.id] if student else []
    else:
        parent = db.scalar(select(Parent).where(Parent.user_id == user.id))
        ids = list(db.scalars(select(Student.id).where(Student.parent_id == (parent.id if parent else -1))).all())
    rows = report_rows(db)
    return {"role": user.role, "children": [r for r in rows if r["student_id"] in ids]}


@router.get("/settings")
def get_school_settings(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    rows = db.scalars(select(SchoolSetting).order_by(SchoolSetting.key)).all()
    settings_map = {row.key: row.value for row in rows}
    settings_map.setdefault("school_name", settings.school_name)
    return settings_map


@router.put("/settings/{key}")
def update_school_setting(key: str, payload: dict, db: Session = Depends(get_db), actor: User = Depends(require_roles("admin"))):
    if key not in {"school_name"}:
        raise HTTPException(status_code=400, detail="Setting is not editable")
    value = payload.get("value")
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise HTTPException(status_code=422, detail="Setting value must be 1-160 characters")
    row = db.scalar(select(SchoolSetting).where(SchoolSetting.key == key))
    if row is None:
        row = SchoolSetting(key=key, value=value.strip())
        db.add(row)
    else:
        row.value = value.strip()
    audit(db, actor, "school_setting.update", "school_setting", row.id or 0, f"key={key}")
    db.commit()
    return {"key": key, "value": row.value}


@router.get("/audit-logs")
def list_audit_logs(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Limit must be between 1 and 500")
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [{"id": row.id, "actor_user_id": row.actor_user_id, "action": row.action, "target_type": row.target_type,
             "target_id": row.target_id, "details": row.details, "created_at": row.created_at} for row in rows]

