# Gaps

**Usable today:** install the plugin, open the panel, paste a personal access token, and click Connect (or save it in widget settings). Create a token opens the Genos account page. Connect writes the shared host store (`Secret Service` `service=genos` + `host=<origin>`, or `~/.config/genos/credentials.json` mode 0600) so `genos auth token` / `genos servers` on the same host can reuse it.

**Blocked until Genos ships device auth:** panel Sign in needs `POST /api/v1/auth/device/codes` and `POST /api/v1/auth/device/tokens` (production currently 404s). Use PAT / Connect until then.

The panel does not run the `genos` program. There are no Discord credentials here.
