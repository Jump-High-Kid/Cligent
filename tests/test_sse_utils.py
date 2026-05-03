"""sse_utils.with_keepalive 단위 테스트 (2026-05-04)."""
from __future__ import annotations

import asyncio

import pytest

from sse_utils import with_keepalive


def _sync_gen_fast():
    yield "data: 1\n\n"
    yield "data: 2\n\n"


async def _async_gen_fast():
    yield "data: a\n\n"
    yield "data: b\n\n"


def _sync_gen_slow():
    """첫 chunk 직후 잠시 sleep — keepalive 발생 유도."""
    import time
    yield "data: start\n\n"
    time.sleep(0.25)
    yield "data: end\n\n"


@pytest.mark.asyncio
async def test_keepalive_passes_sync_chunks():
    out = []
    async for chunk in with_keepalive(_sync_gen_fast(), interval=10.0):
        out.append(chunk)
    assert out == ["data: 1\n\n", "data: 2\n\n"]


@pytest.mark.asyncio
async def test_keepalive_passes_async_chunks():
    out = []
    async for chunk in with_keepalive(_async_gen_fast(), interval=10.0):
        out.append(chunk)
    assert out == ["data: a\n\n", "data: b\n\n"]


@pytest.mark.asyncio
async def test_keepalive_emits_ping_on_idle():
    """interval보다 긴 idle 사이에 ': ping\\n\\n' 한 번 이상 yield."""
    out = []
    async for chunk in with_keepalive(_sync_gen_slow(), interval=0.05):
        out.append(chunk)
    pings = [c for c in out if c == ": ping\n\n"]
    data = [c for c in out if c.startswith("data:")]
    assert data == ["data: start\n\n", "data: end\n\n"]
    assert len(pings) >= 1


@pytest.mark.asyncio
async def test_keepalive_propagates_exception():
    def _boom():
        yield "data: ok\n\n"
        raise RuntimeError("boom")

    out = []
    with pytest.raises(RuntimeError, match="boom"):
        async for chunk in with_keepalive(_boom(), interval=10.0):
            out.append(chunk)
    assert out == ["data: ok\n\n"]
