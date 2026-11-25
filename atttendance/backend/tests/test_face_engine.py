import numpy as np
import pytest
import sys

from app.ai import face_engine
from app.ai.face_engine import FaceInputError, FaceModelUnavailable, match_embedding, similarity


def test_embedding_similarity_and_threshold(monkeypatch):
    monkeypatch.setattr("app.ai.face_engine.settings.face_match_threshold", 0.9)
    candidates = [(7, "Student", np.array([1.0, 0.0], dtype=np.float32))]
    assert match_embedding(np.array([1.0, 0.0], dtype=np.float32), candidates) == (7, "Student", 1.0)
    assert match_embedding(np.array([0.0, 1.0], dtype=np.float32), candidates) is None
    assert similarity(np.array([1.0]), np.array([1.0, 0.0])) == 0.0


def test_invalid_image_is_rejected():
    with pytest.raises(FaceInputError):
        face_engine.extract_embedding(b"not an image")


def test_missing_model_has_explicit_setup_error(monkeypatch):
    face_engine._get_model.cache_clear()
    monkeypatch.setattr("app.ai.face_engine.settings.insightface_model", "not-installed-test-model")
    monkeypatch.setattr("app.ai.face_engine.settings.insightface_root", "./missing-test-model")
    monkeypatch.setitem(sys.modules, "insightface", None)
    with pytest.raises(FaceModelUnavailable, match="could not initialize"):
        face_engine._get_model()
    face_engine._get_model.cache_clear()

