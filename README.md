# Genos

Omarchy bar widget for the Genos servers on your account. Each row shows the server name, game name, status, and selected profile name when present. Lifecycle actions are Start, Stop, and Restart. When a server is **Stopped**, the panel also offers **Change profile** (list setups, select, or unload).

The widget calls the Genos HTTP API itself. It does not run the `genos` program.

## Install

```sh
omarchy plugin add https://github.com/AnthonyPoschen/genos-omarchy --enable
omarchy bar move io.github.anthonyposchen.genos --section right
omarchy restart shell
```

**After any plugin add, update, or remove/re-add you must run `omarchy restart shell`.** `omarchy-shell shell rescanPlugins` alone is not enough for this bar widget: Omarchy’s Quickshell build does not reliably drop compiled QML for the same file path ([omacom/omarchy#8555](https://github.com/omacom/omarchy/issues/8555)). Without a shell restart the panel can keep an old `Panel.qml` (top-right version missing/wrong, or no `v…` caption) even when `~/.config/omarchy/plugins/io.github.anthonyposchen.genos/manifest.json` is already the new version.

To pick up a newer plugin version:

```sh
omarchy plugin remove io.github.anthonyposchen.genos
omarchy plugin add https://github.com/AnthonyPoschen/genos-omarchy --enable
omarchy restart shell
```

Or, if the plugin is already installed as a git checkout:

```sh
omarchy plugin update io.github.anthonyposchen.genos --yes
omarchy restart shell
```

**Verify load:** open the Genos panel. The top-right corner must show `v2026.9.25+5` (or the version in `manifest.json`). Status stays human (`Authentication not configured`, `Waiting for approval — code …`, etc.) without a plugin stamp. If the corner version is missing or wrong, the shell is still on stale QML — run `omarchy restart shell` again.

After **Sign in**, approve in the browser even if the panel closes. Reopen: you should be authenticated (server list or Signed in), not leftover Your code / Open again with no token.

## What it talks to

Override the API host with the `GENOS_HOST` environment variable (default `https://genosservers.com`). There is no Genos site field in Settings. When `GENOS_HOST` is unset, the helper also accepts `currentHost` in `$XDG_CONFIG_HOME/genos/config.toml` (default `~/.config/genos/config.toml`); `current` is used only when `currentHost` is absent. The origin must be `https`, except `http` for `localhost`, `127.0.0.1`, or `genos.localhost`.

With a token it calls:

- `GET /api/v1/servers`
- `POST /api/v1/servers/{id}/actions` with an `Idempotency-Key` and `{"type":"start|stop|restart","confirmUnsavedProgressLoss":false}`
- `GET /api/v1/servers/{id}/setups` (helper `setups`)
- `PUT /api/v1/servers/{id}/selected-setup` with `{"setupID","expectedSelectedSetupID"}` (helper `select-setup`)
- `DELETE /api/v1/servers/{id}/selected-setup` with `{"expectedSelectedSetupID"}` (helper `unload-setup`)

Those three profile helpers hit only the setups / selected-setup endpoints above.

Start is sent immediately. Stop asks for confirmation and the question names the server. Restart asks only when `playerCount` is greater than zero or `notableUpdates` is not empty.

**Change profile** is shown only when the row status is `Stopped`. Select and unload ask for confirmation (CLI: `--confirm` or `--confirmed`, same as Stop). The panel refuses client-side when status is not Stopped; the API conflict code `server_not_confirmed_stopped` is surfaced in the status line when returned.

**Primary auth:** click **Sign in**. Device auth (`POST /api/v1/auth/device/codes` and `…/tokens`) is live on production. The panel opens the approval page, shows your user code while waiting, and stores the token in the shared host store (`Secret Service` attribute `host`, or `credentials.json`) so `genos` on the same machine can reuse it.

**Fallback:** paste a personal access token (PAT) in Settings and save it (or **Manage tokens** → account page, then Connect). Use PAT / Connect when device Sign in is unavailable.

Responses larger than 256 KiB are refused. A list of more than 64 servers is refused rather than cut short. Credentialed requests are not redirected.

## Credentials

The first match wins:

The bar widget saves the token you paste in its own settings. A command that does not pass that setting still checks, in order:

1. `GENOS_TOKEN`, when it is set and not empty.
2. A Secret Service item with service `genos` and attribute `host` equal to the API origin. The helper runs `/usr/bin/secret-tool lookup service genos host <origin>`. The token is read from stdout, not from the command line. The `username` attribute is not used.
3. `$XDG_CONFIG_HOME/genos/credentials.json` (default `~/.config/genos/credentials.json`), and only when that file is mode `0600`. If it is group or world readable, the panel refuses it and tells you to `chmod 0600` it. The file looks like `{"hosts":{"https://origin":{"token":"..."}}}`.

`~/.config/genos/local.env` is not read. A token is not passed on a command line, written into the log, or kept on a QML property after the request finishes.

**Sign in** (device login) and **Connect** (paste PAT) store the token with `secret-tool` (token on stdin). If the keyring is unavailable, they write the credentials file at mode `0600` and say they did. Connect reads the pasted token from the helper's stdin. Prefer **Sign in**; use Connect / paste PAT as a fallback.

## Obtaining a token

Genos API routes expect an `Authorization: Bearer …` token for your account.

1. **Primary:** click **Sign in** in the panel. Approve the device in the browser (user code is shown while waiting). Production serves `POST /api/v1/auth/device/*`.
2. **Fallback:** open **Manage tokens** in Settings (Genos `/account`), mint a personal access token, paste it, and click **Connect** (or save it in widget settings). **Remove token from this widget** only clears the bar setting — it does not open a browser. You can also store a token with `genos auth token`.

## Removing

```sh
omarchy plugin remove io.github.anthonyposchen.genos
```

Removing the plugin does not delete credentials. These stay:

- the credentials file, `$XDG_CONFIG_HOME/genos/credentials.json` (default `~/.config/genos/credentials.json`), if it exists
- the Secret Service item for service `genos` whose `host` attribute is the API origin, if it exists

`config.toml` in that directory is also left in place. The plugin reads it and does not own it. Nothing else is written: no cache, no service, and no Discord credentials.
