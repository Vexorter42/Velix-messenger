<div align="center">

# Velix

**A self-hosted messenger you can actually run yourself.**
One small Python server, a Windows desktop app, and a mobile web client — all speaking the same WebSocket protocol.

[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/desktop-Windows-0078d6?logo=windows&logoColor=white)](#windows-app)
[![Mobile](https://img.shields.io/badge/mobile-PWA-5288c1?logo=pwa&logoColor=white)](#mobile-app)
[![TLS](https://img.shields.io/badge/transport-TLS%201.3-31a24c)](#encryption)
[![Languages](https://img.shields.io/badge/interface-EN%20%2F%20RU-a695e7)](#interface-language)

English · [Русский](README.ru.md)

<img src="docs/screenshot-chat.png" alt="Velix desktop client" width="760">

</div>

---

## What it is

Velix is a private chat for a handful of people who know each other: a family, a
group of friends, a small team. You run the server yourself — on a Raspberry Pi
at home, on a VPS, on any machine that stays on — and nobody else holds your
messages.

Everything is deliberately small and boring: one SQLite file for history, plain
files for attachments, no message queue, no container stack, no cloud account.
The server is a single `python server.py` away.

**What you get**

| | |
|---|---|
| 💬 **Conversations** | Groups you create and invite people into, plus one-to-one direct chats. Reply quotes, reactions, copy, delete-your-own, full-text search, typing indicator, who-is-online. |
| ✓✓ **Delivery ticks** | One grey tick — the server took it. Two grey — it reached everyone in the conversation. Two blue — everyone read it. |
| 📷 **Attachments** | Photos, GIFs (animated in place), video and any other file. Tap a photo to open it full-window. Paste a screenshot straight from the clipboard. Images are compressed server-side — a 7.5 MB phone photo lands at ~400 KB. |
| 👤 **Accounts** | Invite-only registration, scrypt password hashing, session tokens, brute-force lockout. Profile with a name, a bio and a photo. |
| 🔒 **Encryption** | TLS 1.3 (`wss://`) with a Let's Encrypt certificate. The client falls back to plain `ws://` only if the server has no certificate — and says so on screen. |
| 📱 **Phones** | An Android app (`Velix.apk`), or the mobile web client the server hands out at the same address — add it to the home screen and it behaves like an app, push notifications included. |
| 🔄 **Updates** | A button in Settings. The server hands out the fresh build, the client swaps itself and restarts — no reinstall. |
| 🌍 **Two languages** | English and Russian, switched in Settings, applied instantly. |

## Quick start

```bash
pip install -r requirements.txt
python server.py
```

The server listens on port 8765 over both IPv4 and IPv6, so it accepts
connections from the same machine and from the local network.

Then start a client — the desktop app:

```bash
python gui.py
```

…or the terminal one, which speaks the same protocol:

```bash
python client.py
```

In the address field you can type a bare host (`velix.example.org`,
`192.168.0.225`) or a host with a port (`velix.example.org:9000`). Without a
port, 8765 is assumed. The client tries `wss://` first and falls back to `ws://`.

Registration needs an invite code — mint one on the server:

```bash
python invite.py "for Maria"    # issue a code
python invite.py --list         # see who used what
```

<div align="center">
<img src="docs/screenshot-signin.png" alt="Sign-in screen" width="420">
<img src="docs/screenshot-settings.png" alt="Settings" width="420">
</div>

## Windows app

The published `Velix.exe` is a single file with Python inside — nothing to
install alongside it. Ready-made builds live on the
[Releases](https://github.com/Vexorter42/Velix-messenger/releases) page:
`VelixSetup.exe` installs into the user profile, so it never asks for
administrator rights, creates Start-menu and desktop shortcuts, and uninstalls
the normal way.

Building it yourself needs `pip install pyinstaller`:

```bash
python -m PyInstaller --noconfirm --onefile --windowed --name Velix --icon icon.ico --add-data "icon.ico;." --collect-all customtkinter --collect-all darkdetect gui.py
```

The installer is compiled from `installer.iss` with Inno Setup 6, with the built
`Velix.exe` sitting next to it:

```bash
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

The binaries are not committed — they are 30-odd megabytes and rebuildable. The
version lives in `version.py` and must match `AppVersion` in `installer.iss`.

## Mobile app

There are two ways onto a phone.

**The Android app.** `Velix.apk` is on the
[Releases](https://github.com/Vexorter42/Velix-messenger/releases) page. It is
signed by hand rather than by a store, so Android will ask permission to install
it. On first launch it asks for the server address and remembers it.

It is a thin shell around the same web client the server hands out — on purpose:
one set of screens for phone and browser means a new feature shows up in both at
once. The trade-off is that a WebView has no Push API, so **notifications with
the app closed only work through the web client below**.

Building it needs the Android SDK and takes no Gradle:

```bash
python android/build.py
```

**The web client.** Nothing to install: the server hands it out at the same
address and port as the chat itself.

```
https://velix.example.org:8765/
```

It opens in any browser on Android and iPhone. Through the browser menu ("Add to
home screen") the page installs like a normal app — its own icon, no address
bar. The session token is remembered, so the password is asked once.

It does what the desktop client does: history, avatars, photos and video from
the gallery or straight from the camera, profile, reactions, replies, search —
and push notifications when the app is closed.

The pages live in `web/` and are served from `server.py` itself, so there is no
second web server and no second port to forward. A plain GET returns a file; a
request carrying `Upgrade: websocket` goes to the chat.

## Interface language

The interface ships in **English by default** and switches to Russian in
Settings (desktop) or in the profile screen (mobile). The choice is remembered
and applied immediately — no restart.

Server-side messages carry a code alongside the Russian text, so an error the
server generated is shown in the language the reader chose. Push notifications
follow the language the phone subscribed with.

Translations live in `i18n.py` (desktop, console) and `web/i18n.js` (mobile).
The key of a translation is the Russian source string, which keeps the code
readable and makes a missing translation obvious.

## Self-hosting

### Encryption

The server speaks `wss://` — the same TLS that protects banking sites. Point it
at a certificate and a key:

```bash
VELIX_CERT=/path/fullchain.pem VELIX_KEY=/path/privkey.pem python server.py
```

Without those variables it starts on plain `ws://` and says so in the log. The
client shows an "not encrypted" mark in the chat header in that case.

For a DuckDNS domain the certificate is issued over a DNS challenge, so port 80
does not need to be free:

```bash
sudo certbot certonly --manual --preferred-challenges dns \
  --manual-auth-hook /usr/local/lib/velix/duckdns-auth.sh \
  --manual-cleanup-hook /usr/local/lib/velix/duckdns-cleanup.sh \
  --deploy-hook /usr/local/lib/velix/velix-deploy.sh \
  -d '*.yourname.duckdns.org'
```

Ask for the wildcard only — a domain and its wildcard need the same TXT record,
and DuckDNS stores just one, so the second request overwrites the first. The
wildcard covers every subdomain anyway.

### Who may connect

By default the server accepts anyone who reaches the port. Set
`VELIX_ALLOWED_HOSTS` and it will only accept connections that arrived under the
listed names:

```bash
VELIX_ALLOWED_HOSTS=velix.example.org,localhost python server.py
```

The name comes from the `Host` header of the handshake; everyone else gets a
`403`. This is not a security boundary — a `Host` header is forged in a minute —
but a scanner sweeping addresses and ports does not know the name and never
reaches the chat.

### Backups

`backup.sh` snapshots the database through `sqlite3 .backup`, so the copy is
consistent even if someone is writing a message at that moment. Attachments are
copied as hard links and cost disk space only once. The last 14 copies are kept
in `~/velix-backups`. Once a day from cron:

```bash
30 4 * * * $HOME/velix/backup.sh >> $HOME/velix-backups/backup.log 2>&1
```

### A test server next to the live one

Paths and the port come from environment variables, so a spare server can live
on the same machine with its own data and never touch the real conversations:

```bash
VELIX_PORT=8766 VELIX_DB=~/velix-test/velix.db VELIX_MEDIA=~/velix-test/media python server.py
```

### Handing out an update

The server keeps `updates/Velix.exe` and `updates/version.txt` next to itself
and tells every client which version it has. If it is newer than the client's,
the Update button in Settings lights up.

```bash
~/velix/publish-update.sh /path/to/Velix.exe 1.8.0
sudo systemctl restart velix
```

The swap works around the fact that a running exe cannot be overwritten but can
be renamed: the old file moves aside as `Velix.exe.old`, the new one takes its
place, the app restarts and cleans up the leftover on the next start. If
anything fails halfway, the old file comes back.

### Environment variables

| Variable | What it does |
|---|---|
| `VELIX_PORT` | Port to listen on (default 8765) |
| `VELIX_DB` | Path to the SQLite file |
| `VELIX_MEDIA` | Directory for attachments |
| `VELIX_WEB` | Directory with the web client |
| `VELIX_CERT`, `VELIX_KEY` | TLS certificate and key |
| `VELIX_ALLOWED_HOSTS` | Comma-separated host names allowed to connect |
| `VELIX_OPEN_REGISTRATION` | `1` drops the invite requirement |
| `VELIX_UPDATES` | Directory with the build handed out to clients |
| `VELIX_PUSH_KEYS` | Path to the VAPID key file |
| `VELIX_LANG` | Interface language of the console client |

## How it is built

| File | What lives there |
|---|---|
| `server.py` | Connections, delivery, presence, the web client, updates |
| `storage.py` | SQLite: messages, users, sessions, conversations, reactions, subscriptions |
| `protocol.py` | The shared language: JSON frames, attachment kinds, size limits |
| `accounts.py` | Passwords, login checks, session tokens |
| `media.py` | Server-side image compression |
| `push.py` | Push notifications through the browser's own service |
| `gui.py` | The desktop client, in Telegram's visual language |
| `client.py` | The terminal client |
| `web/` | The mobile web client |
| `android/` | The Android app and its Gradle-free build script |
| `i18n.py`, `web/i18n.js` | Interface translations |
| `store.py` | What the client remembers between runs |
| `tray.py`, `autostart.py` | Tray icon, start with Windows |
| `updates.py`, `version.py` | Updating in place |
| `invite.py` | Invite codes |
| `backup.sh`, `publish-update.sh`, `test-server.sh` | Server-side chores |

A message travels as a JSON text frame; the bytes of an attachment follow in a
separate binary frame. The desktop client keeps the network on its own thread
with its own asyncio loop and talks to the interface through a queue — Tkinter
must not be touched from another thread.

Attachment payloads are never pushed to a client on their own: history carries
only the description, and the bytes are requested when it is time to show them.
Video is fetched only when the button is pressed, so opening a chat does not
drag in every clip at once.

## Limits, honestly

- **No end-to-end encryption.** Messages are protected in transit, but they sit
  in the clear on the server — whoever controls the machine can read them.
- **Groups have no admins**: anyone in a group can invite anyone else, and
  nobody can be removed.
- **No flood protection** and no message length limit.
- **The history file is not encrypted**: anyone with access to `velix.db` has
  the whole correspondence.

It is a private messenger for people who trust the person running the server —
not a tool for keeping secrets from that person.
