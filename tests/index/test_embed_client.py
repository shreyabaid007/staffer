"""Unit tests for dsm.index.embed_client — mocked Modal boundary, no network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dsm.index.embed_client import EmbedError, ModalEmbedClient


@pytest.fixture()
def mock_modal_cls():
    """Patch modal.Cls.from_name to return a controllable mock."""
    with patch("dsm.index.embed_client.modal.Cls.from_name") as from_name:
        instance = MagicMock()
        from_name.return_value = lambda: instance
        yield instance


class TestEmbedClientEmbed:
    def test_returns_vectors(self, mock_modal_cls: MagicMock) -> None:
        mock_modal_cls.embed.remote.return_value = [
            [0.1] * 768,
            [0.2] * 768,
        ]
        client = ModalEmbedClient()
        result = client.embed(["text a", "text b"])
        assert len(result) == 2
        assert len(result[0]) == 768

    def test_passage_mode_forwarded(self, mock_modal_cls: MagicMock) -> None:
        mock_modal_cls.embed.remote.return_value = [[0.1] * 768]
        client = ModalEmbedClient()
        client.embed(["some text"], mode="passage")
        mock_modal_cls.embed.remote.assert_called_once_with(["some text"], "passage")

    def test_query_mode_forwarded(self, mock_modal_cls: MagicMock) -> None:
        mock_modal_cls.embed.remote.return_value = [[0.1] * 768]
        client = ModalEmbedClient()
        client.embed(["some query"], mode="query")
        mock_modal_cls.embed.remote.assert_called_once_with(["some query"], "query")

    def test_modal_error_wrapped(self, mock_modal_cls: MagicMock) -> None:
        import modal.exception

        mock_modal_cls.embed.remote.side_effect = modal.exception.Error("boom")
        client = ModalEmbedClient()
        with pytest.raises(EmbedError, match="Modal embed call failed"):
            client.embed(["fail"])


class TestEmbedClientRerank:
    def test_returns_scores(self, mock_modal_cls: MagicMock) -> None:
        mock_modal_cls.rerank.remote.return_value = [0.9, 0.3, 0.1]
        client = ModalEmbedClient()
        result = client.rerank("query", ["doc1", "doc2", "doc3"])
        assert result == [0.9, 0.3, 0.1]

    def test_args_forwarded(self, mock_modal_cls: MagicMock) -> None:
        mock_modal_cls.rerank.remote.return_value = [0.5]
        client = ModalEmbedClient()
        client.rerank("my query", ["passage"])
        mock_modal_cls.rerank.remote.assert_called_once_with("my query", ["passage"])

    def test_modal_error_wrapped(self, mock_modal_cls: MagicMock) -> None:
        import modal.exception

        mock_modal_cls.rerank.remote.side_effect = modal.exception.Error("boom")
        client = ModalEmbedClient()
        with pytest.raises(EmbedError, match="Modal rerank call failed"):
            client.rerank("query", ["doc"])
