# Architecture

The project has three clients of one REST service. FastAPI owns identity, authorization, consent, class enrollment, attendance sessions, reporting, and biometric matching. SQLAlchemy persists domain records in PostgreSQL (SQLite is supported for local development and tests). Alembic migrations own the schema.

The face engine decodes a submitted image, uses InsightFace for detection and normalized embeddings, compares only against consented profiles enrolled in the active class, and reports an unknown result below a configurable cosine threshold. It lazy-loads the real model and returns an explicit service error when model initialization/inference is unavailable. Embeddings are Fernet-encrypted at rest. Raw image bytes are processed in memory and are not written to disk or included in audit details/log messages.

The React/Vite web application and Expo client use bearer JWTs and role-aware API endpoints. Web camera access uses `getUserMedia`; mobile captures with Expo Camera and posts multipart image data to the backend. Both require camera permission and a reachable HTTPS API outside local development.

## Trust and liveness boundaries

InsightFace recognition is not proof of liveness. No anti-spoof model is bundled. `AUTO_MARK_RECOGNIZED` defaults to false so the teacher confirms an identified student through the audited manual attendance endpoint. The optional `LIVENESS_PROVIDER=module:function` extension must be a server-side checker that receives image bytes and returns the literal `True` only when liveness is validated. Automatic marking requires both this checker and `AUTO_MARK_RECOGNIZED=true`; failures never mark attendance. Do not enable automatic attendance until the checker is independently validated and false-match behavior is tested. Manual attendance remains available without biometrics.

## Operational boundaries

Use HTTPS, strict CORS, a secret manager, database backups, rate limiting, account lockout/monitoring, and administrative review in a production deployment. JWT logout is client-side token disposal because access tokens are stateless; use short expiration and a revocation strategy if immediate server-side logout is required.

