import os

os.environ["DATABASE_URL"] = "sqlite:///./test-smartattend.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production-123456"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User
from app.security import hash_password

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    with TestingSession() as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture()
def admin_user(db):
    admin = User(
        email="admin@example.com",
        full_name="Admin",
        password_hash=hash_password("very-secure-password"),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin

