"""Map :class:`GatewayError` to operator-facing FastAPI HTTP exceptions."""

from __future__ import annotations

from fastapi import HTTPException, status

from .gateway_client import (
    GATEWAY_MAX_CONCURRENT_RUNS,
    GatewayError,
    is_gateway_concurrency_limit,
)


def http_exception_from_gateway_start(
    exc: GatewayError,
    *,
    action_label: str = "启动 Agent",
) -> HTTPException:
    """Translate a failed ``POST /v1/runs`` into a structured HTTP error."""
    if is_gateway_concurrency_limit(exc):
        return HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "gateway_concurrency_limit",
                "message": (
                    f"当前同时运行的 Agent 任务已达上限（最多 "
                    f"{GATEWAY_MAX_CONCURRENT_RUNS} 个）。请等待其他任务完成后再试，"
                    "或在页面右下角的 Agent 会话面板中停止不需要的任务。"
                ),
                "max_concurrent_runs": GATEWAY_MAX_CONCURRENT_RUNS,
            },
        )
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        {
            "code": "gateway_start_failed",
            "message": f"{action_label}失败，请稍后重试",
            "detail": str(exc),
        },
    )
