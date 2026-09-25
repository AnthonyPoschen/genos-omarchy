# Gaps

**Usable today:** install the plugin, open the panel, and click **Sign in**. Device auth is live on production (`POST /api/v1/auth/device/codes` and `…/tokens`). The panel opens the approval page, shows your user code while waiting, and stores the token in the shared host store (`Secret Service` `service=genos` + `host=<origin>`, or `~/.config/genos/credentials.json` mode 0600) so `genos auth token` / `genos servers` on the same host can reuse it.

**Fallback:** paste a personal access token and use Connect (or save it in widget settings). **Manage tokens** opens the Genos account page (`/account`). Prefer Sign in; use PAT / Connect when device Sign in is unavailable. After plugin install/update, run `omarchy restart shell` so QML reloads ([omacom/omarchy#8555](https://github.com/omacom/omarchy/issues/8555)).

The panel does not run the `genos` program. There are no Discord credentials here.

Override the API host with `GENOS_HOST` (default `https://genosservers.com`); Settings no longer has a Genos site field.
