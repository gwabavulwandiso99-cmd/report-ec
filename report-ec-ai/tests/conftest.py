"""Shared pytest fixtures."""
import pytest

from helpers import Chat, Clock
from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.chatbot.session_store import InMemorySessionStore
from report_ec_ai.integration.backend_adapter import MockBackendAdapter


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def backend():
    return MockBackendAdapter()


@pytest.fixture
def engine(backend, clock):
    return ConversationEngine(backend, InMemorySessionStore(now=clock), now=clock)


@pytest.fixture
def chat(engine):
    return Chat(engine)
