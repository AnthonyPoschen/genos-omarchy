# Gaps

A person installs the plugin, left-clicks it, and pastes a personal access token. Create a token opens the account page. The token is stored in the bar widget settings. There are no Discord credentials here.

The panel does not run the `genos` program. `genos auth login` is a separate way to store a token this panel can read from the keyring or the credentials file. Connect in the panel reads a pasted token from the helper's stdin and stores it the same way.
