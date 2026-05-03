"""SSE 스트리밍 유틸 — keepalive 래퍼 (2026-05-04).

목적: iOS/Android 캐리어·NAT·리버스 프록시가 idle TCP 연결을 끊는 것을 방어.
SSE 코멘트 라인(`: ping\\n\\n`)은 클라이언트에서 자동 무시되므로 안전.

Caddy/uvicorn 환경에서 검증: 15초 간격이면 일반적인 NAT idle 타임아웃(60s+)을
충분히 방어하면서 트래픽 영향 미미.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
from typing import AsyncGenerator, Iterator, Union

DEFAULT_KEEPALIVE_INTERVAL = 15.0


async def with_keepalive(
    gen: Union[Iterator, AsyncGenerator],
    interval: float = DEFAULT_KEEPALIVE_INTERVAL,
) -> AsyncGenerator[str, None]:
    """SSE generator(sync 또는 async)를 감싸 idle 시 keepalive 코멘트 발송.

    Args:
        gen: SSE 프레임 문자열을 yield하는 generator (`data: ...\\n\\n` 형식).
             동기 generator는 별도 thread에서 실행되어 메인 이벤트 루프 블로킹 방지.
        interval: keepalive 발송 주기 (초). 기본 15s.

    Yields:
        원본 chunk 또는 `: ping\\n\\n` (interval 내 chunk 없을 때).
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    sentinel = object()

    if inspect.isasyncgen(gen):
        async def _drain() -> None:
            try:
                async for chunk in gen:
                    await queue.put(chunk)
            except Exception as exc:
                await queue.put(("__err__", exc))
            finally:
                await queue.put(sentinel)

        task: asyncio.Task = asyncio.create_task(_drain())
        thread: threading.Thread | None = None
    else:
        loop = asyncio.get_running_loop()

        def _producer() -> None:
            try:
                for chunk in gen:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(chunk), loop,
                    ).result()
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(
                    queue.put(("__err__", exc)), loop,
                ).result()
            finally:
                asyncio.run_coroutine_threadsafe(
                    queue.put(sentinel), loop,
                ).result()

        thread = threading.Thread(target=_producer, daemon=True)
        thread.start()
        task = None  # type: ignore[assignment]

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is sentinel:
                return
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and item[0] == "__err__"
            ):
                raise item[1]  # type: ignore[misc]
            yield item
    finally:
        if task is not None and not task.done():
            task.cancel()
