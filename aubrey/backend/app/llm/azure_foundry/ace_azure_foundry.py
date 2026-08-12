import asyncio
from typing import Any
from collections.abc import AsyncIterator
from ...utils.common.logger import Logger
from ...utils.azure.azure_helpers import AzureOpenAIClient
from ...config.application_context import get_application_context


logger = Logger().get_logger()


class AceAzureFoundry:
    def __init__(self):
        ac = get_application_context()
        foundry_cfg = ac.microsoft["azure"]["azure_foundry"]
        embedding_cfg = foundry_cfg.get("embedding", {})
        text_completion_cfg = foundry_cfg.get("text_completion", {})

        self.base_endpoint = foundry_cfg["base_endpoint"]
        self.api_key = foundry_cfg["api_key"]
        self.timeout_seconds = foundry_cfg.get("timeout_seconds")

        self.embedding_deployment = embedding_cfg.get("deployment")
        self.embedding_api_version = embedding_cfg.get("api_version")
        self.embedding_dimensions = embedding_cfg.get("embedding_dimensions")

        self.chat_deployment = text_completion_cfg.get("deployment")
        self.chat_api_version = text_completion_cfg.get("api_version")
        self.max_output_tokens = int(text_completion_cfg.get("max_tokens"))

        self.azure_client_factory = AzureOpenAIClient(
            api_key=self.api_key,
            base_endpoint=self.base_endpoint,
            timeout_seconds=self.timeout_seconds,
        )


    def create_embeddings(
        self,
        texts: list[str],
        *,
        dimensions: int | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, Any] = {
            "model": self.embedding_deployment,
            "input": texts,
        }

        resolved_dimensions = dimensions or self.embedding_dimensions
        if resolved_dimensions:
            kwargs["dimensions"] = resolved_dimensions

        client = self.azure_client_factory.get_client(
            deployment=self.embedding_deployment,
            api_version=self.embedding_api_version,
        )
        response = client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]

    async def acreate_embeddings(
        self,
        texts: list[str],
        *,
        dimensions: int | None = None,
    ) -> list[list[float]]:
        return await asyncio.to_thread(
            self.create_embeddings, texts, dimensions=dimensions
        )


    async def acomplete_chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        """Non-streaming completion — for structured outputs (e.g. entity
        extraction) where the full response is parsed as one piece."""
        deployment = model or self.chat_deployment
        client = self.azure_client_factory.get_async_client(
            deployment=deployment,
            api_version=self.chat_api_version,
        )
        response = await client.chat.completions.create(
            messages=messages,
            model=deployment,
            max_completion_tokens=max_output_tokens or self.max_output_tokens,
        )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    async def astream_chat(
        self,
        *,
        messages: list[dict[str, str]] | None = None,
        question: str | None = None,
        instructions: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        if messages is None:
            if question is None or instructions is None:
                raise ValueError(
                    "astream_chat requires either `messages` or both "
                    "`question` and `instructions`."
                )
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": question},
            ]

        deployment = model or self.chat_deployment
        resolved_max = max_output_tokens or self.max_output_tokens

        client = self.azure_client_factory.get_async_client(
            deployment=deployment,
            api_version=self.chat_api_version,
        )

        stream = await client.chat.completions.create(
            messages=messages,
            model=deployment,
            max_completion_tokens=resolved_max,
            stream=True,
        )

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield text
