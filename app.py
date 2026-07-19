from __future__ import annotations

import hmac
import logging
import threading
from dataclasses import asdict

from flask import Flask, jsonify, request

from ai import AIServiceError
from config import Settings
from security import UnsafeTargetError
from service import SiteResearchService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("site-agent")

settings = Settings.from_env()
app = Flask(__name__)
job_lock = threading.BoundedSemaphore(value=1)


def _authorized() -> bool:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    supplied = header[len(prefix):].strip()
    return bool(settings.agent_secret) and hmac.compare_digest(supplied, settings.agent_secret)


@app.get("/")
def home():
    return jsonify(
        service="vk-site-ai-agent",
        status="running",
        configured=not settings.validate(),
        model=settings.openrouter_model,
        search_mode=settings.resolved_search_mode,
    )


@app.get("/health")
def health():
    errors = settings.validate()
    return jsonify(
        ok=not errors,
        service="vk-site-ai-agent",
        model=settings.openrouter_model,
        search_mode=settings.resolved_search_mode,
        configuration_errors=errors,
    ), (200 if not errors else 503)


@app.post("/ask")
def ask():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    errors = settings.validate()
    if errors:
        return jsonify(ok=False, error="configuration_error", details=errors), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="invalid_json"), 400

    question = str(payload.get("question", "")).strip()
    deep = bool(payload.get("deep", False))
    if not question:
        return jsonify(ok=False, error="question_required"), 400
    if len(question) > 1200:
        return jsonify(ok=False, error="question_too_long"), 400

    if not job_lock.acquire(blocking=False):
        return jsonify(ok=False, error="agent_busy"), 429

    try:
        logger.info("Research started. deep=%s question=%r", deep, question[:160])
        service = SiteResearchService(settings)
        result = service.research(question, deep=deep)
        logger.info(
            "Research finished. backend=%s urls=%d pages=%d errors=%d elapsed=%.1fs confidence=%s",
            result.search_backend,
            result.urls_discovered,
            result.pages_scanned,
            result.errors,
            result.elapsed_seconds,
            result.confidence,
        )
        return jsonify(ok=True, **asdict(result))
    except UnsafeTargetError as exc:
        logger.warning("Unsafe target rejected: %s", exc)
        return jsonify(ok=False, error="unsafe_site", details=str(exc)), 400
    except AIServiceError as exc:
        logger.warning("AI error: %s", exc)
        return jsonify(ok=False, error="ai_error", details=str(exc)), 502
    except Exception as exc:
        logger.exception("Unhandled research error")
        return jsonify(ok=False, error="internal_error", details=str(exc)[:500]), 500
    finally:
        job_lock.release()
