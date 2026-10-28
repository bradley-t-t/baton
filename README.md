# Baton

A Claude Code hook that adds answers from a second model you configure.

## Install

```bash
curl -fsSL https://taylorurl.com/baton | sh
```

This installs the `baton` command with pipx or uv (it offers to install uv into `~/.local/bin` if neither is there), removes an old pip install of claude-handoff, adds the bin folder to your PATH, then runs `baton setup` and `baton doctor`. It never uses sudo. Needs macOS or Linux.

Or by hand, with Python 3.9 or newer:

```bash
pipx install git+https://github.com/bradley-t-t/baton.git   # or: uv tool install git+...
baton setup
```

## Setup

```bash
baton setup
baton doctor
```

`setup` asks for a provider, its server URL (local only) and its API key, tests the connection, and adds Baton's hooks to `~/.claude/settings.json`. Everything else comes from the config file below. Start a new Claude Code session afterward.

By default the provider's newest flagship model is used and its commands run with full permissions. Change either in the config file, or with `--model` and `--commands`. Re-running `setup` keeps your edits; `--reset` puts the model and commands back to the defaults.

Without prompts:

```bash
XAI_API_KEY=... baton setup --provider grok --yes
baton setup --provider local --base-url http://localhost:11434/v1 --yes
```

| Flag | |
| :--- | :--- |
| `--provider` | `grok`, `openai`, `anthropic` or `local` |
| `--model`, `--base-url` | Model name (`auto` by default); server URL for `local` |
| `--api-key-env VAR` | Read the key from `VAR` (default: the provider's usual variable) |
| `--commands` | `on` (any command, the default), `off`, `read-only` or `custom` |
| `--allow` | Comma-separated commands for `--commands custom` |
| `--reset` | Model and commands back to the defaults |
| `--no-test` | Skip the connection test |
| `--dry-run` | Show what would change and stop |
| `--yes` | Don't ask for confirmation |

`baton doctor --online` also sends a test prompt.

## Config

`~/.config/baton/config.json`. `setup` writes it, and you can edit it by hand.

| Key | Default | |
| :--- | :--- | :--- |
| `enabled` | `true` | Off switch |
| `provider` | `""` | Name of an entry in `providers`. Nothing is sent while it's empty. |
| `providers.<name>` | | `type` (`xai`, `openai`, `anthropic`, `openai_compatible`), `model`, `api_key_env`, `base_url` |
| `providers.<name>.model` | `"auto"` | `"auto"` is the provider's newest flagship, read from its model list once a day (a local server: the first model it lists). Or a model name. |
| `timeout_seconds` | `300` | How long to wait for the provider |
| `judge.enabled` | `"auto"` | A second check on whether Claude declined. `"auto"` uses it when the provider has a key or is local. |
| `run_actions.enabled` | `true` | Run a `bash` block from the provider's reply in Claude's working directory |
| `run_actions.allow` | `"all"` | `"all"`, or a list of the commands that may run. With a list, a block with any other command doesn't run at all. |
| `run_actions.read_only` | | Set by `--commands read-only`; also refuses writing to files with `>` |
| `run_actions.max_commands`, `run_actions.timeout_seconds` | `20`, `60` | Most commands in one block; how long a block may run |
| `install.remove_old` | `true` | `install.sh` removes an old pip install of claude-handoff |
| `never_handoff_patterns` | `[]` | Regexes; a request or reply that matches is never sent |
| `context.user_messages`, `context.tool_calls` | `4`, `10` | How much recent context is sent with a request |

Environment overrides: `BATON=0` (off), `BATON_PROVIDER`, `BATON_MODEL`, `BATON_TIMEOUT`, `BATON_JUDGE=0`, `BATON_RUN_ACTIONS=0|1`, `BATON_CONFIG`, `BATON_STATE`.

## Privacy

API keys are stored in `~/.config/baton/secrets.env` with mode 600, or read from your environment. Each key is only sent to its own provider, and keys are left out of the environment of any command Baton runs.

When Claude declines a request, Baton sends that request, your last few messages and recent tool calls (clipped) to the provider you chose, and shows its answer in place of Claude's reply, marked `(via <provider>)`. The Claude Code transcript is not changed. Logs stay on your machine in `~/.local/state/baton`.

## Uninstall

```bash
baton uninstall --all
```

This removes Baton's hooks, deletes `~/.config/baton` (config and keys), `~/.local/state/baton` and Baton's backups of `settings.json`, then removes the `baton` command with `pipx uninstall claude-baton` or `uv tool uninstall claude-baton`, whichever installed it. Other hooks in `settings.json` are left alone.

`baton uninstall` on its own only removes the hooks, and `--purge` also deletes the config, keys, logs and backups.

Baton used to be called claude-handoff. `baton setup` or `baton install` replaces the old hooks and copies the old config.

To run the tests from a checkout: `python3 -m unittest`.

MIT licensed, see [LICENSE](LICENSE).
