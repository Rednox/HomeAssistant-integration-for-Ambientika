"""API Client.

This uses https://pypi.org/project/ambientika/ as a facade.
Ambientika API: https://app.ambientika.eu:4521/swagger/index.html.
"""

import logging
import asyncio
import aiohttp
from typing import Any, Dict, List, Optional

from ambientika_py import authenticate, Device
from returns.result import Failure, Success
from returns.primitives.exceptions import UnwrapFailedError

from .const import AmbientikaApiClientAuthenticationError, AmbientikaApiClientError, DEFAULT_HOST

LOGGER = logging.getLogger(__name__)


class AmbientikaApiClientError(Exception):
    """Exception to indicate a general API error."""


class AmbientikaApiClient:
    """API Client Class."""

    def __init__(self, username: str, password: str) -> None:
        """Create an instance of the API."""
        self._username = username
        self._password = password
        self._host = DEFAULT_HOST
        self._api_client = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector = aiohttp.TCPConnector(
            limit=5,              # Reduced concurrent connections to avoid overwhelming the server
            limit_per_host=1,     # Only 1 connection per host to reduce server load
            ttl_dns_cache=300,    # Cache DNS lookups for 5 minutes
            enable_cleanup_closed=True,
            force_close=False,    # Enable keep-alive
            keepalive_timeout=60  # Keep connections alive for 60 seconds
        )

        # Configure much longer timeouts to handle slow server responses
        self._timeout = aiohttp.ClientTimeout(
            total=30,        # Much longer total timeout - server can be very slow
            connect=10,      # Longer connection timeout
            sock_connect=10, # Longer socket connection timeout
            sock_read=20     # Much longer read timeout for slow responses
        )

    async def _ensure_client(self):
        """Ensure we have an authenticated API client."""
        if self._api_client is None:
            LOGGER.debug("Authenticating with Ambientika API.")

            # Create persistent session with timeout and connection pooling
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=self._timeout,
                    connector=self._connector,
                    headers={
                        "Connection": "keep-alive",
                        "User-Agent": "HomeAssistant-Ambientika/1.0"
                    }
                )

            max_retries = 2  # Reduced retries to fail faster with long timeouts
            retry_count = 0
            last_error = None

            while retry_count < max_retries:
                try:
                    authenticator = await authenticate(
                        self._username, self._password, self._host
                    )

                    # Handle authentication failure
                    if isinstance(authenticator, Failure):
                        error_msg = str(authenticator.failure())
                        raise AmbientikaApiClientAuthenticationError(
                            f"Authentication failed: {error_msg}"
                        )

                    self._api_client = authenticator.unwrap()

                    # CRITICAL FIX: Replace the session in the underlying API to use our persistent session
                    # This prevents the library from creating new sessions for every API call
                    if hasattr(self._api_client, '_api') and self._session:
                        # Store original session cleanup function
                        original_session = getattr(self._api_client._api, '_session', None)

                        # Monkey-patch the API methods to use our persistent session
                        self._patch_api_methods(self._api_client._api)

                        # Clean up the original session if it exists
                        if original_session and original_session != self._session:
                            try:
                                await original_session.close()
                            except Exception as e:
                                LOGGER.warning(f"Error closing original session: {str(e)}")

                    # Successfully set up client, break the retry loop
                    break

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = e
                    retry_count += 1
                    if retry_count < max_retries:
                        wait_time = min(5 + retry_count * 3, 15)  # Longer backoff: 5s, 8s, 11s...
                        LOGGER.warning(f"Authentication attempt {retry_count} failed: {str(e)}. Retrying in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                        continue

                    # Clean up session on final failure
                    if self._session and not self._session.closed:
                        await self._session.close()
                        self._session = None
                    self._api_client = None
                    raise AmbientikaApiClientError(f"Connection failed after {max_retries} attempts: {str(last_error)}")
                except Exception as e:
                    # Clean up session on unexpected error
                    if self._session and not self._session.closed:
                        await self._session.close()
                        self._session = None
                    self._api_client = None
                    if isinstance(e, AmbientikaApiClientAuthenticationError):
                        raise
                    raise AmbientikaApiClientError(f"Failed to setup API client: {str(e)}")

    def _patch_api_methods(self, api):
        """Patch the AmbientikaApi methods to use our persistent session."""
        original_get = api.get
        original_post = api.post

        async def patched_get(path: str, params: dict = None):
            if params is None:
                params = {}
            headers = {"Authorization": f"Bearer {api.token}"}

            try:
                async with self._session.get(
                    url=f"{api.host}/{path}",
                    headers=headers,
                    params=params
                ) as response:
                    from ambientika_py import parse_response_body
                    data = await parse_response_body(response)
                    if response.status == 200:
                        from returns.result import Success
                        return Success(data)
                    else:
                        from returns.result import Failure
                        return Failure({"status_code": response.status, "data": data})
            except Exception as e:
                from returns.result import Failure
                return Failure({"status_code": 0, "data": str(e)})

        async def patched_post(path: str, body: dict):
            headers = {"Authorization": f"Bearer {api.token}"}

            try:
                async with self._session.post(
                    url=f"{api.host}/{path}",
                    headers=headers,
                    json=body
                ) as response:
                    from ambientika_py import parse_response_body
                    data = await parse_response_body(response)
                    if response.status == 200:
                        from returns.result import Success
                        return Success(data)
                    else:
                        from returns.result import Failure
                        return Failure({"status_code": response.status, "data": data})
            except Exception as e:
                from returns.result import Failure
                return Failure({"status_code": 0, "data": str(e)})

        # Replace the methods
        api.get = patched_get
        api.post = patched_post


    async def async_get_data(self) -> List[Device]:
        """Get all devices from the API.

        The devices are flattened. Meaning, the information about rooms and houses is not made available to hass.
        """
        try:
            await self._ensure_client()
            LOGGER.debug("fetching houses.")

            houses = await self._api_client.houses()
            if isinstance(houses, Failure):
                error_msg = str(houses.failure())
                LOGGER.error(f"Failed to fetch houses: {error_msg}")
                await self._cleanup()  # Force re-auth on next try
                raise AmbientikaApiClientError(f"Ambientika API error: {error_msg}")

            try:
                house_list = houses.unwrap()
                if not house_list:
                    LOGGER.warning("No houses found in the response")
                    return []

                LOGGER.debug(f"Found {len(house_list)} houses")

                # Extract all devices from all houses and rooms
                devices = []
                for house in house_list:
                    if not house:
                        continue

                    LOGGER.debug(f"Processing house: {house.name}")
                    if not hasattr(house, 'rooms'):
                        LOGGER.warning(f"House {house.name} has no rooms attribute")
                        continue

                    for room in house.rooms:
                        if not room:
                            continue

                        LOGGER.debug(f"Processing room: {room.name} with {len(room.devices) if hasattr(room, 'devices') else 0} devices")
                        if hasattr(room, 'devices'):
                            devices.extend([d for d in room.devices if d])

                LOGGER.debug(f"Total devices found: {len(devices)}")
                return devices

            except UnwrapFailedError as e:
                LOGGER.error(f"Failed to unwrap houses response: {e}")
                await self._cleanup()
                raise AmbientikaApiClientError("Failed to process houses data")

        except aiohttp.ClientError as exception:
            LOGGER.error(f"Connection error: {str(exception)}")
            await self._cleanup()  # Force re-auth on next try
            raise AmbientikaApiClientError(
                f"Connection error: {str(exception)}"
            ) from exception
        except aiohttp.ServerTimeoutError as exception:
            LOGGER.error(f"Server timeout error: {str(exception)}")
            await self._cleanup()  # Force re-auth on next try
            raise AmbientikaApiClientError(
                f"Server timeout: {str(exception)}"
            ) from exception
        except asyncio.TimeoutError as exception:
            LOGGER.error(f"Request timeout error: {str(exception)}")
            await self._cleanup()  # Force re-auth on next try
            raise AmbientikaApiClientError(
                f"Request timeout: {str(exception)}"
            ) from exception
        except Exception as exception:
            LOGGER.error(f"Unknown error: {str(exception)}")
            await self._cleanup()  # Force re-auth on next try
            raise AmbientikaApiClientError(f"Unknown error: {str(exception)}") from exception

    async def _cleanup(self):
        """Internal cleanup of resources."""
        try:
            # Clean up the API client first
            if self._api_client:
                self._api_client = None

            # Clean up our persistent session
            if self._session and not self._session.closed:
                try:
                    await self._session.close()
                except Exception as e:
                    LOGGER.warning(f"Error closing persistent session: {str(e)}")
                finally:
                    self._session = None

        except Exception as e:
            LOGGER.error(f"Error during cleanup: {str(e)}")
        finally:
            self._api_client = None
            self._session = None

    async def close(self):
        """Close the API client and cleanup resources."""
        await self._cleanup()

        # Also close the connector
        if self._connector and not self._connector.closed:
            try:
                await self._connector.close()
            except Exception as e:
                LOGGER.warning(f"Error closing connector: {str(e)}")
