from functools import lru_cache

import cv2
import numpy as np
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class FaceModelUnavailable(RuntimeError):
    pass


class FaceInputError(ValueError):
    pass


@lru_cache(maxsize=1)
def _get_model():
    try:
        from insightface.app import FaceAnalysis

        model = FaceAnalysis(name=settings.insightface_model, root=settings.insightface_root, providers=["CPUExecutionProvider"])
        model.prepare(ctx_id=-1, det_size=(640, 640))
        return model
    except Exception as exc:
        raise FaceModelUnavailable(
            "InsightFace could not initialize. Install the backend requirements and provision the configured "
            f"'{settings.insightface_model}' model under '{settings.insightface_root}'. No recognition was performed."
        ) from exc


def _fernet() -> Fernet:
    if not settings.biometric_encryption_key:
        raise FaceModelUnavailable("Set BIOMETRIC_ENCRYPTION_KEY before enrolling or recognizing biometric profiles.")
    try:
        return Fernet(settings.biometric_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise FaceModelUnavailable("BIOMETRIC_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def extract_embedding(image_bytes: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FaceInputError("Uploaded data is not a supported image.")
    try:
        faces = _get_model().get(image)
    except FaceModelUnavailable:
        raise
    except Exception as exc:
        raise FaceModelUnavailable("Face model inference failed; no attendance was recorded.") from exc
    if len(faces) != 1:
        raise FaceInputError("Exactly one clearly detected face is required.")
    embedding = np.asarray(faces[0].normed_embedding, dtype=np.float32)
    if embedding.ndim != 1 or not np.isfinite(embedding).all():
        raise FaceInputError("The model returned an invalid face embedding.")
    norm = float(np.linalg.norm(embedding))
    if norm <= 0:
        raise FaceInputError("The model returned an empty face embedding.")
    return embedding / norm


def encrypt_embedding(embedding: np.ndarray) -> bytes:
    return _fernet().encrypt(np.asarray(embedding, dtype=np.float32).tobytes())


def decrypt_embedding(ciphertext: bytes) -> np.ndarray:
    try:
        raw = _fernet().decrypt(ciphertext)
    except InvalidToken as exc:
        raise FaceModelUnavailable("Stored biometric profile could not be decrypted; check the configured key.") from exc
    vector = np.frombuffer(raw, dtype=np.float32)
    if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all():
        raise FaceModelUnavailable("Stored biometric profile is invalid.")
    return vector


def similarity(first: np.ndarray, second: np.ndarray) -> float:
    first = np.asarray(first, dtype=np.float32)
    second = np.asarray(second, dtype=np.float32)
    if first.shape != second.shape or first.ndim != 1:
        return 0.0
    denom = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denom <= 0:
        return 0.0
    return float(np.dot(first, second) / denom)


def match_embedding(query: np.ndarray, profiles: list[tuple[int, str, np.ndarray]]) -> tuple[int, str, float] | None:
    best: tuple[int, str, float] | None = None
    for student_id, name, candidate in profiles:
        score = similarity(query, candidate)
        if best is None or score > best[2]:
            best = (student_id, name, score)
    if best and best[2] >= settings.face_match_threshold:
        return best
    return None

