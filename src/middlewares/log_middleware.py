import uuid
import json
import os
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message


LOG_DIR = "logs"
LOG_FILE = "requests.log"
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("request_logger")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(os.path.join(LOG_DIR, LOG_FILE), encoding="utf-8")

formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
file_handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(file_handler)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        method = request.method
        path = request.url.path

        # была проблема с ордерами смотрел только их
        if path == '/api/v1/order':

            body_bytes = await request.body()
            body_str = body_bytes.decode("utf-8", errors="ignore")

            try:
                parsed_body = json.loads(body_str)
            except Exception:
                parsed_body = body_str

            logger.info(f"[{request_id}] ➡️ {method} {path} | Body: {parsed_body if method in ['POST', 'DELETE'] else 'N/A'}")

            async def receive() -> Message:
                return {"type": "http.request", "body": body_bytes}

            request._receive = receive

            try:
                response: Response = await call_next(request)

                # Перехватываем тело ответа
                response_body = b""
                async for chunk in response.body_iterator:
                    response_body += chunk

                new_response = Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
                new_response.headers["X-Request-ID"] = request_id

                try:
                    parsed_response = json.loads(response_body.decode("utf-8"))
                except Exception:
                    parsed_response = response_body[:300].decode("utf-8", errors="ignore")

                logger.info(f"[{request_id}] ⬅️ {response.status_code} | Response: {parsed_response}")
                return new_response

            except Exception as e:
                logger.error(f"[{request_id}] ❌ Exception: {repr(e)}")
                raise
        return await call_next(request)