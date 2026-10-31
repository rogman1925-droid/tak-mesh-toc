# mesh_mailroute: server phones → mesh phones, private DMs

**mesh_mailroute** is a small helper for **OpenTAKServer**, and part of TAK MESH TOC. It runs on the
OpenTAKServer computer, next to RabbitMQ. With it installed and `DM_RELAY = 1` on the deck, a phone on the server
and a phone on the mesh DM each other the normal ATAK way: open the contact, type, send. No contact list or chat
changes.

## Why it's needed
- **How OpenTAKServer files DMs:** it has a mailbox (a RabbitMQ queue) for every phone connected to it, and files
  each DM under the recipient's callsign.
- **Why mesh phones get nothing:** they aren't connected to the server. The deck's bridge carries all of them over
  one connection, so a DM for a mesh callsign has no mailbox to land in, and RabbitMQ throws it away.
- **What the helper does:** it adds one **mail rule** per mesh callsign, *DMs for that callsign go into the deck's
  mailbox*. The bridge then hands each DM to that one phone over the mesh.

## What it does, and what it never does
| | |
|---|---|
| Learns mesh callsigns | From OpenTAKServer's own `firehose`: position reports that arrive on the deck's connection |
| Adds a rule | `dms` exchange, key = mesh callsign → the deck's mailbox |
| Skips | Any callsign that is connected to the server itself. If a mesh phone also connects directly (cellular, Wi-Fi), its rule is removed so it doesn't get DMs twice. The rule comes back when that phone disconnects. |
| Re-adds | Every 30 s if a rule went missing (RabbitMQ forgets them on restart) |
| Forgets | A callsign not heard behind the deck for 24 h |
| Answers the DM route check | The deck sends a tiny test message to a made-up callsign (`SELFTEST`, default `DECK-selftest`) every minute. The helper puts it back into the deck's mailbox. When it comes back, the whole server→mesh DM path is proven to work. The deck shows **DM route: OK / BROKEN**. |
| **Never** | Changes OpenTAKServer's own rules or mailboxes. It only touches the rules it made, which are listed in `~/.mesh_mailroute.json`. |

**Normal server traffic is unaffected.** A rule only matches DMs addressed to that mesh callsign, which the server
would otherwise throw away.
- **With the deck off:** those DMs wait in the deck's mailbox, and are delivered if the deck comes back before the
  server's message lifetime (24 h by default) runs out.
- **With the helper off:** the rules it already made keep working.

## Install (on the OpenTAKServer computer)
Copy this folder to the server. Start the deck's bridge once first, so the server knows the deck's name. Then run:
```
cd mesh_mailroute && bash install_server.sh --dry-run
bash install_server.sh
journalctl -u mesh-mailroute -n 20 --no-pager
```
The installer:
- uses OpenTAKServer's own Python (it already has `pika`)
- finds the deck's mailbox name (`DECK-…`). You can also give it: `--deck-uid DECK-<deck hostname>`
- copies OpenTAKServer's RabbitMQ login, only if it was changed from the default
- sets up the `mesh-mailroute` service, which starts after RabbitMQ and OpenTAKServer

**Show the rules:** `~/.opentakserver_venv/bin/python ~/mesh_mailroute/mesh_mailroute.py --list`

**Undo everything** (removes the service and every rule it made; the server is untouched): `bash uninstall_server.sh`

## Tested
- **Test setup:** a real RabbitMQ broker with pika, and a stand-in that follows OpenTAKServer 1.7's own mailbox code.
  - `tests/mailrule_test.py` tests the mail rule on its own.
  - The deck package's `tests/dm_relay_e2e.py` and `tests/dm_route_e2e.py` run the real bridge, Chat and this
    helper, with mesh phones and server phones.
- **Real hardware:** used with OpenTAKServer 1.7 on a Raspberry Pi 5.
