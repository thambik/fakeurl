from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str
    role: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class StudentCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    student_number: str = Field(min_length=1, max_length=50)
    class_id: int
    parent_id: int | None = None


class StudentUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    student_number: str | None = Field(default=None, min_length=1, max_length=50)
    parent_id: int | None = None
    class_id: int | None = None


class TeacherCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    employee_code: str = Field(min_length=1, max_length=50)


class TeacherUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    employee_code: str | None = Field(default=None, min_length=1, max_length=50)


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    section: str = Field(min_length=1, max_length=40)
    teacher_id: int | None = None


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    section: str | None = Field(default=None, min_length=1, max_length=40)
    teacher_id: int | None = None


class ClassOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    section: str
    teacher_id: int | None


class ParentCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    phone: str | None = None


class ConsentIn(BaseModel):
    consent: bool


class SessionCreate(BaseModel):
    class_id: int


class ManualAttendanceIn(BaseModel):
    student_id: int
    status: str = Field(pattern="^(present|absent|late|excused)$")


class RecognitionOut(BaseModel):
    matched: bool
    student_id: int | None = None
    student_name: str | None = None
    similarity: float | None = None
    attendance_marked: bool = False
    liveness_checked: bool = False
    detail: str


class AttendanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int
    student_id: int
    status: str
    source: str
    recorded_at: datetime


class ReportRow(BaseModel):
    student_id: int
    student_number: str
    student_name: str
    present: int
    absent: int
    total_sessions: int
    percentage: float


class DateRange(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

