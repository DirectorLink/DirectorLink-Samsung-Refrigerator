# Security

This driver controls Samsung refrigerators through the homeowner's SmartThings account. Security reports are welcome and are handled first.

## Reporting a problem

Please report privately on GitHub: **Security → Report a vulnerability** in this repository. If you can't, open an issue that only says you have something to report, without the details, and we'll find a private way to talk.

Say what you found, where (file and line), and what someone could do with it. Please don't test against homes or SmartThings accounts that aren't yours.

## Supported versions

Fixes go into the latest release. Update the driver in Composer Pro with the `.c4z` from that release.

## How the driver handles credentials

- **Personal Access Token:** used only to create the OAuth app. New tokens expire after 24 hours on their own.
- **OAuth tokens:** kept in Control4's encrypted driver storage (`C4:PersistSetValue(..., true)`), never in properties, and never written to the log, even with Debug Mode on.
- **Client Secret:** a masked Composer property. Anyone with Composer access to the project can still read it.
- **Network:** the driver talks only to `api.smartthings.com` and `auth-global.api.smartthings.com`, over HTTPS with certificate checks.
- **Revoking access:** remove *DirectorLink Samsung Refrigerator* from the connected services in the SmartThings app, or run the driver action **Sign Out (clear tokens)**.
