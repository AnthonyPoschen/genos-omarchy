# Gaps

A person installs the plugin, then creates a token or approves the device login the panel shows. The keyring path needs a running Secret Service (`org.freedesktop.secrets`, for example gnome-keyring) and `secret-tool`. There are no Discord credentials here.

The panel does not run the `genos` program. `genos auth login` is a separate way to store a token this panel can read from the keyring or the credentials file. Connect in the panel reads a pasted token from the helper's stdin and stores it the same way.
