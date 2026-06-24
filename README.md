# aiohifiberry

Async Python client for the HiFiBerry OS / HBOS NG AudioControl REST API.

This package is intended to hold the device communication layer used by a Home
Assistant Core integration. It does not import Home Assistant.

## Example

```python
from aiohttp import ClientSession

from aiohifiberry import AudioControlClient


async with ClientSession() as session:
    client = AudioControlClient(session, "hifiberry.local")
    await client.async_update()
    print(client.now_playing)
    await client.async_command("pause")
```

## Supported API Areas

- Connection validation.
- Now-playing and player discovery.
- Active-player capability detection.
- Play, pause, stop, next, and previous commands.
- Volume state and volume commands.
- HBOS NG cover-art lookup.

HBOS NG normally exposes AudioControl through the public web UI route:

```text
http://<device>/api/audiocontrol/
```

Some builds also expose backend services on custom ports. The client defaults
to port `80` and falls back to the public route for proxied endpoints when a
non-80 port is supplied.

