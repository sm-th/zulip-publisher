# Deploying the publisher

The publisher is one long-running Python process on the operator's Mac. Config is
machine-local (`zulip_publisher.toml`, gitignored); secrets come from SecretSpec.

## Wizard

```bash
nix develop
./scripts/deploy.sh
```

Five stages: write `zulip_publisher.toml` from confirmed defaults, set the
SecretSpec secrets (`secretspec check`), verify GitHub SSH, run a
`PUBLISHER_DRY_RUN=1` pass, then install and load a `launchd` agent.

## Secrets (SecretSpec)

Declared in `secretspec.toml`; set them once with `secretspec check`:

| Secret | Source |
| --- | --- |
| `ZULIP_URL` | Zulip realm base URL |
| `ZULIP_API_KEY` | publisher bot API key |
| `ZULIP_API_USERNAME` | publisher bot email |
| `PREPARE_URL` | deployed Worker URL (from prepare-markdown deploy) |
| `PREPARE_TOKEN` | bearer token set on the Worker |
| `R2_ACCESS_KEY_ID` | R2 access key for image renditions |
| `R2_SECRET_ACCESS_KEY` | R2 secret key |

## Run modes

```bash
nix run .#debug   -- once        # SecretSpec + Python dev mode (dry-run capable)
nix run .#sandbox -- once        # inside a Microsandbox microVM
PUBLISHER_DRY_RUN=1 nix develop -c secretspec run -- python -m zulip_publisher once
```

## Persistent service (macOS launchd)

The wizard installs `~/Library/LaunchAgents/com.example.zulip-publisher.plist`,
which runs `run-publisher.sh` (the poll loop) at login and keeps it alive.

```bash
launchctl load   ~/Library/LaunchAgents/com.example.zulip-publisher.plist   # start
launchctl unload ~/Library/LaunchAgents/com.example.zulip-publisher.plist   # stop
tail -f publisher.log                                                          # logs
```
