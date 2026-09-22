# Genos

Omarchy bar widget for the Genos servers on your account. Each row shows the server name, game name, and status. The only actions are Start, Stop, and Restart.

The widget calls the Genos HTTP API itself. It does not run the `genos` program.

## Install

```sh
omarchy plugin add https://github.com/AnthonyPoschen/genos-omarchy --enable
omarchy bar move io.github.anthonyposchen.genos --section right
```

## What it talks to

The origin is `GENOS_HOST` when that is set, otherwise `currentHost` in `$XDG_CONFIG_HOME/genos/config.toml` (default `~/.config/genos/config.toml`). `current` is used only when `currentHost` is absent. The origin must be `https`, except `http` for `localhost`, `127.0.0.1`, or `genos.localhost`.

With a token it calls:

- `GET /api/v1/servers`
- `POST /api/v1/servers/{id}/actions` with an `Idempotency-Key` and `{"type":"start|stop|restart","confirmUnsavedProgressLoss":false}`

Start is sent immediately. Stop asks for confirmation and the question names the server. Restart asks only when `playerCount` is greater than zero or `notableUpdates` is not empty.

Sign in calls:

- `POST /api/v1/auth/device/codes`
- `POST /api/v1/auth/device/tokens`

Responses larger than 256 KiB are refused. A list of more than 64 servers is refused rather than cut short. Credentialed requests are not redirected.

## Credentials

The first match wins:

1. `GENOS_TOKEN`, when it is set and not empty.
2. A Secret Service item with service `genos` and attribute `host` or `username` equal to the API origin. The helper runs `/usr/bin/secret-tool lookup` with those attributes as separate arguments. The token is read from stdout, not from the command line.
3. `$XDG_CONFIG_HOME/genos/credentials.json` (default `~/.config/genos/credentials.json`), and only when that file is mode `0600`. If it is group or world readable, the panel refuses it and tells you to `chmod 0600` it. The file looks like `{"hosts":{"https://origin":{"token":"..."}}}`.

`~/.config/genos/local.env` is not read. A token is not passed on a command line, written into the log, or kept on a QML property after the request finishes.

Device login and Connect store the token with `secret-tool` (the token is written to its stdin). If the keyring is unavailable, they write the credentials file at mode `0600` and say they did. Connect reads the pasted token from the helper's stdin.

## Removing

```sh
omarchy plugin remove io.github.anthonyposchen.genos
```

Removing the plugin does not delete credentials. These stay:

- the credentials file, `$XDG_CONFIG_HOME/genos/credentials.json` (default `~/.config/genos/credentials.json`), if it exists
- the Secret Service item for service `genos` whose `host` or `username` attribute is the API origin, if it exists

`config.toml` in that directory is also left in place. The plugin reads it and does not own it. Nothing else is written: no cache, no service, and no Discord credentials.
