"""RabbitMQ dispatcher and worker for automatic standard-form publication."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.knowledge_governance import KnowledgePublishJob

logger = logging.getLogger(__name__)


class KnowledgePublishBroker:
    def __init__(self, publish_item: Callable[[str, int | None], Awaitable[None]]) -> None:
        self._publish_item = publish_item
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None
        self._exchange: aio_pika.abc.AbstractRobustExchange | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not settings.KNOWLEDGE_RABBITMQ_URL.strip():
            logger.warning(
                "Knowledge RabbitMQ worker disabled: KNOWLEDGE_RABBITMQ_URL is not configured; using in-process fallback poller"
            )
            self._task = asyncio.create_task(
                self._poll_local_fallback(), name="knowledge-publish-local-poller"
            )
            return
        try:
            self._connection = await aio_pika.connect_robust(settings.KNOWLEDGE_RABBITMQ_URL)
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=1)
            self._exchange = await self._channel.declare_exchange(
                settings.KNOWLEDGE_RABBITMQ_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True,
            )
            queue = await self._channel.declare_queue(settings.KNOWLEDGE_RABBITMQ_QUEUE, durable=True)
            await queue.bind(self._exchange, routing_key="form.publish")
            await queue.consume(self._consume)
            self._task = asyncio.create_task(self._dispatch_pending(), name="knowledge-publish-dispatcher")
            logger.info("Knowledge RabbitMQ worker started")
        except Exception:
            logger.exception("Knowledge RabbitMQ connection failed; falling back to in-process poller")
            self._task = asyncio.create_task(
                self._poll_local_fallback(), name="knowledge-publish-local-poller"
            )

    async def _poll_local_fallback(self) -> None:
        while not self._stopping.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    jobs = list((await session.scalars(select(KnowledgePublishJob).where(
                        KnowledgePublishJob.status == "PENDING"
                    ).limit(25))).all())
                for job in jobs:
                    try:
                        await self._publish_item(job.item_id, job.target_version)
                    except Exception:
                        logger.exception("Knowledge local publication failed itemId=%s", job.item_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Knowledge local poller failed; will retry")
            await asyncio.sleep(2)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await self._task
        if self._connection is not None:
            await self._connection.close()

    async def _dispatch_pending(self) -> None:
        while not self._stopping.is_set():
            try:
                async with AsyncSessionLocal() as session, session.begin():
                    jobs = list((await session.scalars(select(KnowledgePublishJob).where(
                        KnowledgePublishJob.status == "PENDING"
                    ).limit(25))).all())
                    for job in jobs:
                        await self._send(job.item_id, job.target_version)
                        job.status = "QUEUED"
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Knowledge publish dispatcher failed; will retry")
            await asyncio.sleep(2)

    async def _send(self, item_id: str, target_version: int) -> None:
        if self._exchange is None:
            raise RuntimeError("Knowledge RabbitMQ exchange is unavailable")
        await self._exchange.publish(
            aio_pika.Message(
                body=json.dumps({"item_id": item_id, "target_version": target_version}).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=f"{item_id}:v{target_version}",
            ),
            routing_key="form.publish",
        )

    async def _consume(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            payload = json.loads(message.body)
            item_id = str(payload["item_id"])
            raw_version = payload.get("target_version")
            target_version = int(raw_version) if raw_version is not None else None
            try:
                await self._publish_item(item_id, target_version)
            except Exception:
                logger.exception("Knowledge publication failed itemId=%s", item_id)


__all__ = ["KnowledgePublishBroker"]
