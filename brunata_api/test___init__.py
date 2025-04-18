"""Test the Brunata API."""
# ruff: noqa: S101, SLF001
from logging import INFO, basicConfig, getLogger
from os import environ

import pytest

from brunata_api import Client, Consumption, Interval

# Set up logging
LOGGER = getLogger(__name__)
basicConfig(level=INFO)

@pytest.mark.asyncio
async def test_fetch_electricity() -> None:
  """Test fetching electricity consumption."""
  client = Client(environ["BRUNATA_USERNAME"], environ["BRUNATA_PASSWORD"])
  for metric in [client._heating, client._water, client._power]:
    assert isinstance(metric, dict)
    assert not metric
  await client.fetch_meters()
  assert client._tokens != {}
  for metric in [client._heating, client._water, client._power]:
    assert isinstance(metric, dict)
    assert metric != {}
  # Fetch data
  await client.fetch_consumption(Consumption.ELECTRICITY, Interval.DAY)
  await client.fetch_consumption(Consumption.ELECTRICITY, Interval.MONTH)
  client.get_consumption()
  # Test token refresh
  client._tokens.pop("access_token")
  await client.fetch_meters()

@pytest.mark.asyncio
async def test_bad_credentials(caplog) -> None:  # noqa: ANN001
  """Test bad credentials."""
  # with pytest.raises(ValueError):
  client = Client("johndoe@example.com", "password")
  await client.fetch_meters()
  assert not client._tokens
  assert "An error has occurred while attempting to authenticate." in caplog.text

# TODO: Test more failure cases
# TODO: Test URL formatter
