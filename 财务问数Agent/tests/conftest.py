import pytest
from agent.settings import Settings


@pytest.fixture(autouse=True)
def forbid_real_key_access(monkeypatch):
    def blocked(self):
        raise AssertionError('测试禁止读取真实Key；请使用模拟模型，不允许自动访问外部模型API。')
    monkeypatch.setattr(Settings,'key',blocked)
