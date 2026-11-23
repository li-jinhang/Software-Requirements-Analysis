import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory):
    # Settings are instantiated on first import and shared by in-process API tests.
    with pytest.MonkeyPatch.context() as env:
        env.setenv("DATA_DIR", str(tmp_path_factory.mktemp("app-data")))
        env.setenv("MOCK_LLM", "true")
        env.setenv("OPENAI_API_KEY", "")
        env.setenv("EMBEDDING_PROVIDER", "local_lite")
        yield
