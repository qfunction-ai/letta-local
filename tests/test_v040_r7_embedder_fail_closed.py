"""v0.16.40 R-7: embedder no-key fallback fails closed.

Without a key, `_prepare_client_kwargs_embedding` substituted a dummy key
while the fallback embedding config pointed at api.openai.com — the request
still left the machine carrying uploaded document chunks (401 at best).
Fail closed instead: cloud endpoint + no key = configuration error, raised
BEFORE any network call. Local endpoints keep the dummy-key convention.

Witnesses: cloud+nokey raises naming the fix; local+nokey proceeds with the
dummy key; cloud+key proceeds with the real key; empty endpoint + no key
proceeds (no destination configured, no silent default).
"""

from unittest.mock import patch

import pytest

from letta.llm_api.openai_client import OpenAIClient
from letta.schemas.embedding_config import EmbeddingConfig


@pytest.fixture
def client():
    return OpenAIClient.__new__(OpenAIClient)


@pytest.fixture
def cloud_config():
    return EmbeddingConfig.default_config(model_name="text-embedding-3-small", provider="openai")


@pytest.fixture
def local_config(cloud_config):
    return cloud_config.model_copy(update={"embedding_endpoint": "http://localhost:11434/v1"})


def _no_key():
    p = patch("letta.llm_api.openai_client.model_settings")
    ms = p.start()
    ms.openai_api_key = None
    return p, ms


class TestEmbedderFailClosed:
    def test_cloud_no_key_raises(self, client, cloud_config):
        p, ms = _no_key()
        try:
            with pytest.raises(ValueError, match="No embedding model configured"):
                client._prepare_client_kwargs_embedding(cloud_config)
        finally:
            p.stop()

    def test_error_names_local_alternative(self, client, cloud_config):
        p, ms = _no_key()
        try:
            with pytest.raises(ValueError, match="Ollama embeddinggemma"):
                client._prepare_client_kwargs_embedding(cloud_config)
        finally:
            p.stop()

    def test_error_names_r7(self, client, cloud_config):
        p, ms = _no_key()
        try:
            with pytest.raises(ValueError, match="R-7"):
                client._prepare_client_kwargs_embedding(cloud_config)
        finally:
            p.stop()

    def test_local_no_key_dummy(self, client, local_config):
        p, ms = _no_key()
        try:
            kwargs = client._prepare_client_kwargs_embedding(local_config)
            assert kwargs["api_key"] == "DUMMY_API_KEY"
            assert kwargs["base_url"] == "http://localhost:11434/v1"
        finally:
            p.stop()

    def test_cloud_with_key_proceeds(self, client, cloud_config):
        with patch("letta.llm_api.openai_client.model_settings") as ms:
            ms.openai_api_key = "sk-test"
            kwargs = client._prepare_client_kwargs_embedding(cloud_config)
            assert kwargs["api_key"] == "sk-test"

    def test_no_endpoint_no_key_proceeds(self, client, cloud_config):
        empty = cloud_config.model_copy(update={"embedding_endpoint": None})
        p, ms = _no_key()
        try:
            kwargs = client._prepare_client_kwargs_embedding(empty)
            assert kwargs["api_key"] == "DUMMY_API_KEY"
        finally:
            p.stop()

    def test_trailing_slash_cloud_still_detected(self, client, cloud_config):
        slashed = cloud_config.model_copy(update={"embedding_endpoint": "https://api.openai.com/v1/"})
        p, ms = _no_key()
        try:
            with pytest.raises(ValueError, match="No embedding model configured"):
                client._prepare_client_kwargs_embedding(slashed)
        finally:
            p.stop()

    def test_env_openai_key_satisfies_cloud(self, client, cloud_config, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        p, ms = _no_key()
        try:
            kwargs = client._prepare_client_kwargs_embedding(cloud_config)
            assert kwargs["api_key"] == "sk-from-env"
        finally:
            p.stop()
