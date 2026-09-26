from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.models.errors import ConfigurationError
from app.runtime import build_runtime


@pytest.mark.asyncio
async def test_runtime_allows_only_one_instance_per_data_directory(tmp_path):
    path = tmp_path / "data"
    first = build_runtime(path)
    with pytest.raises(ConfigurationError, match="Another Agent instance"):
        build_runtime(path)
    await first.stop()
    await first.start()
    with pytest.raises(ConfigurationError, match="Another Agent instance"):
        build_runtime(path)
    await first.stop()
    second = build_runtime(path)
    second.secrets_repository.update(dedup_hmac_key=SecretStr("abcd"))
    assert len(second.ensure_dedup_key()) >= 32
    await second.stop()
