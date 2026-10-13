"""One explicit retry for transient provider failures; every attempt is counted."""
import asyncio
from openai import APIConnectionError, APITimeoutError, RateLimitError
from .observability import diagnostic


async def response_with_retry(client, parameters, ctx, stage, *, extraction=False):
    try:
        return await client.responses.create(**parameters)
    except (APIConnectionError,APITimeoutError,RateLimitError) as exc:
        ctx.event('model.transient_retry',stage=stage,message=diagnostic(exc),attempt=2)
        await asyncio.sleep(1)
        if extraction:
            ctx.consume('model')
        else:
            ctx.usage.model_calls += 1
        ctx.usage.model_calls_without_usage += 1
        return await client.responses.create(**parameters)
