"""Tests for the HiFiBerry AudioControl client."""
from __future__ import annotations

from collections import defaultdict
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from aiohttp import ClientResponseError, RequestInfo
from yarl import URL

from aiohifiberry import AudioControlClient, AudioControlError


class FakeResponse:
    """Small aiohttp response stand-in."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        """Initialize the response."""
        self._payload = payload or {}
        self.status = status
        self.content_type = content_type

    async def __aenter__(self) -> "FakeResponse":
        """Enter the response context."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit the response context."""

    def raise_for_status(self) -> None:
        """Raise for non-success responses."""
        if self.status >= 400:
            raise ClientResponseError(
                RequestInfo(
                    URL("http://example.test"),
                    "GET",
                    headers={},
                    real_url=URL("http://example.test"),
                ),
                (),
                status=self.status,
            )

    async def json(self) -> dict[str, Any]:
        """Return JSON data."""
        return self._payload


class FakeSession:
    """Small aiohttp session stand-in."""

    def __init__(self) -> None:
        """Initialize route storage."""
        self.routes: dict[tuple[str, str], list[FakeResponse]] = defaultdict(list)
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def add(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        status: int = 200,
    ) -> None:
        """Register a fake response."""
        self.routes[(method, url)].append(FakeResponse(payload, status=status))

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Return the next registered response."""
        self.requests.append((method, url, kwargs))
        responses = self.routes[(method, url)]
        if not responses:
            return FakeResponse(status=404)
        if len(responses) == 1:
            return responses[0]
        return responses.pop(0)


class AudioControlClientTest(IsolatedAsyncioTestCase):
    """Test the AudioControl client."""

    async def test_validate_uses_hbosng_now_playing_fallback(self) -> None:
        """Validation tries older paths before the HBOS NG now-playing route."""
        session = FakeSession()
        session.add(
            "GET",
            "http://speaker:80/api/audiocontrol/now-playing",
            {"state": "idle"},
        )
        client = AudioControlClient(session, "speaker")

        await client.async_validate()

        self.assertTrue(client.connected)
        self.assertIn(
            ("GET", "http://speaker:80/api/audiocontrol/now-playing", {}),
            session.requests,
        )

    async def test_update_reads_now_playing_players_volume_and_cover_art(self) -> None:
        """Update caches all player data and finds cover art."""
        session = FakeSession()
        session.add(
            "GET",
            "http://speaker:80/api/now-playing",
            {
                "state": "playing",
                "player": {
                    "name": "shairport",
                    "state": "playing",
                    "capabilities": ["play", "pause", "metadata", "album_art"],
                },
                "song": {
                    "title": "Big Love",
                    "artist": "Fleetwood Mac \u2022 Video Available",
                    "album": "Greatest Hits",
                },
            },
        )
        session.add(
            "GET",
            "http://speaker:80/api/players",
            {
                "players": [
                    {"name": "shairport", "state": "playing", "is_active": True}
                ]
            },
        )
        session.add("GET", "http://speaker:80/api/volume/state", {"percentage": 55})
        session.add(
            "GET",
            "http://speaker:80/api/audiocontrol/coverart/song/QmlnIExvdmU/RmxlZXR3b29kIE1hYw",
            {
                "results": [
                    {
                        "images": [
                            {
                                "url": "small.jpg",
                                "grade": 1,
                                "width": 100,
                                "height": 100,
                            },
                            {
                                "url": "large.jpg",
                                "grade": 2,
                                "width": 500,
                                "height": 500,
                            },
                        ]
                    }
                ]
            },
        )
        client = AudioControlClient(session, "speaker")

        await client.async_update()

        self.assertTrue(client.connected)
        self.assertEqual(client.now_playing["state"], "playing")
        self.assertEqual(client.players[0]["name"], "shairport")
        self.assertEqual(client.volume["percentage"], 55)
        self.assertEqual(client.cover_art_url, "large.jpg")
        self.assertEqual(client.active_player_name, "shairport")
        self.assertEqual(client.last_active_player_name, "shairport")

    async def test_non_80_public_route_fallback(self) -> None:
        """A custom backend port falls back to the public web UI route."""
        session = FakeSession()
        session.add(
            "GET",
            "http://speaker/api/audiocontrol/now-playing",
            {"state": "idle"},
        )
        client = AudioControlClient(session, "speaker", 1080)

        await client._request("GET", "/api/audiocontrol/now-playing")

        self.assertEqual(
            session.requests[0][1],
            "http://speaker:1080/api/audiocontrol/now-playing",
        )
        self.assertEqual(
            session.requests[1][1],
            "http://speaker/api/audiocontrol/now-playing",
        )

    async def test_command_uses_active_player_name(self) -> None:
        """Commands use the active player name rather than the player id."""
        session = FakeSession()
        client = AudioControlClient(session, "speaker")
        client.now_playing = {
            "state": "playing",
            "player": {
                "name": "spotify",
                "id": "librespot",
                "state": "playing",
                "capabilities": ["next"],
            },
        }
        session.add(
            "POST",
            "http://speaker:80/api/audiocontrol/player/spotify/command/next",
            {},
        )

        await client.async_command("next")

        self.assertEqual(
            session.requests,
            [
                (
                    "POST",
                    "http://speaker:80/api/audiocontrol/player/spotify/command/next",
                    {},
                )
            ],
        )

    async def test_pause_uses_pause_all_before_player_command(self) -> None:
        """Pause uses HiFiBerry's global pause endpoint first."""
        session = FakeSession()
        client = AudioControlClient(session, "speaker")
        client.now_playing = {"state": "playing"}
        session.add("POST", "http://speaker:80/api/audiocontrol/players/pause-all", {})

        await client.async_command("pause")

        self.assertEqual(
            session.requests[0][1],
            "http://speaker:80/api/audiocontrol/players/pause-all",
        )

    async def test_command_error_is_ignored_when_state_changes(self) -> None:
        """Some HBOS NG commands work despite returning an HTTP error."""
        session = FakeSession()
        client = AudioControlClient(session, "speaker")
        client.now_playing = {
            "state": "paused",
            "player": {
                "name": "spotify",
                "state": "paused",
                "capabilities": ["play"],
            },
        }
        client._last_active_player_name = "spotify"
        session.add(
            "POST",
            "http://speaker:80/api/audiocontrol/player/spotify/command/play",
            status=500,
        )
        session.add(
            "GET",
            "http://speaker:80/api/now-playing",
            {"state": "playing", "player": {"name": "spotify", "state": "playing"}},
        )
        session.add("GET", "http://speaker:80/api/players", {"players": []})
        session.add("GET", "http://speaker:80/api/volume/state", {"percentage": 50})

        async def _no_sleep(_: float) -> None:
            return None

        with patch("aiohifiberry.client.asyncio.sleep", _no_sleep):
            await client.async_command("play")

        self.assertEqual(client.now_playing["state"], "playing")

    async def test_unsupported_command_raises(self) -> None:
        """A command with no compatible active player raises."""
        session = FakeSession()
        client = AudioControlClient(session, "speaker")
        client.now_playing = {
            "state": "playing",
            "player": {"name": "shairport", "capabilities": ["pause"]},
        }

        with self.assertRaises(AudioControlError):
            await client.async_command("next")
