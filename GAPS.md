# Gaps

**Usable today:** install the plugin, open the panel, and click **Sign in**. Device auth is live on production (`POST /api/v1/auth/device/codes` and `…/tokens`). The panel opens the approval page, shows your user code while waiting, and stores the token in the shared host store (`Secret Service` `service=genos` + `host=<origin>`, or `~/.config/genos/credentials.json` mode 0600) so `genos auth token` / `genos servers` on the same host can reuse it.

**Fallback:** paste a personal access token and use Connect (or save it in widget settings). Create a token opens the Genos account page. Prefer Sign in; use PAT / Connect when device Sign in is unavailable.

The panel does not run the `genos` program. There are no Discord credentials here.
