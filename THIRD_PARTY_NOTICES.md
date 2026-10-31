# Third-party software

TAK MESH TOC is Copyright (C) 2026 Alejandro Rocha, licensed under the GNU General Public License v3.0 or later
(see `LICENSE`).

To install without internet access, it bundles these libraries, unchanged, in `vendor/`. Each one keeps its own
license, and its license file ships next to it in `vendor/<name>-<version>.dist-info/`.

| Library | Version | License | Copyright | Used for |
|---|---|---|---|---|
| [PyTAK](https://github.com/snstac/pytak) | 7.6.1 | Apache License 2.0 | Sensors & Signals LLC | The connection to the TAK server, and the mesh multicast reader |
| `pytak/asyncio_dgram.py` (inside PyTAK) | | MIT | Justin Bronder | Asyncio UDP sockets |
| [takproto](https://github.com/snstac/takproto) | 3.0.1 | MIT | Delta Bravo-15 | Converting TAK Protocol (protobuf) mesh packets to CoT XML |
| [protobuf](https://github.com/protocolbuffers/protobuf) (pure-Python build) | 6.33.6 | BSD 3-Clause | Google Inc. | Used by takproto |

All three licenses are compatible with GPL-3.0.

## Not bundled
Separate programs that this package works with. They are installed separately and are never modified:
- **OpenTAKServer** (GPL-3.0). `server/mesh_mailroute` runs next to it and uses its RabbitMQ.
  - `tests/ots_intake_sim.py` reproduces OpenTAKServer's stream-reading loop, so tests can show what the server
    accepts.
- **RabbitMQ**, **pika**, **MediaMTX**, **gpsd**, **Tailscale**, and **ATAK**.

## Fonts (the pro look)
Bundled in `common/fonts/` (Latin subsets, WOFF2, from the Fontsource 5.3.0 packages). Each is licensed under the
SIL Open Font License 1.1; the full license text for each is next to the fonts.

| Font | Copyright | License file |
|---|---|---|
| Barlow Condensed (500, 600, 700) | Copyright 2017 The Barlow Project Authors | `common/fonts/OFL-Barlow-Condensed.txt` |
| IBM Plex Sans (400, 500, 600) | Copyright 2019 IBM Corp. | `common/fonts/OFL-IBM-Plex-Sans.txt` |
| IBM Plex Mono (400, 500) | Copyright 2019 IBM Corp. | `common/fonts/OFL-IBM-Plex-Mono.txt` |
