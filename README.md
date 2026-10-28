# Baton

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) turns down a request, Baton sends it to another model you pick (Grok, OpenAI, Anthropic, a local server) and shows that answer where the refusal would have been.

## How a refusal becomes an answer

Three hooks. `UserPromptSubmit` only remembers what you asked and adds nothing to Claude's context. `MessageDisplay` does the work. `Stop` is the backstop.

A reply reaches `MessageDisplay` before it's drawn. Whether it refused the request is one question, and one thing answers it: a judge model, which reads the reply against what you asked. The judge is `"auto"`, so it's on only when the provider has a key or is a local server. It runs with `BATON_JUDGE_ACTIVE` set, which switches the handoff off inside its own call so it can't recurse. A one-line status before a tool call ("Looking at the file first.") is left for `Stop` to judge at the end of the turn, because judging it here would race the provider against Claude doing the work.

On a refusal the request goes to the provider with the last 4 messages and 10 tool calls, each clipped. The hook then draws the answer in place of the refusal, and anything Claude says after it in that turn is drawn as a zero-width space. `displayContent` changes only what's on screen, so the transcript and the model keep the original text. The hook gets 120 seconds, enough for a slow provider and its commands.

`Stop` hands off only when display didn't, which happens when display timed out or never saw the reply. It never blocks, so you don't get "Hook re-prompted Claude" rows.

When Anthropic's safeguards stop a turn, Claude Code shows "API Error: ... safeguards flagged this message" where the reply would be. That's a refusal too, so your message for that turn goes to the provider and its answer is drawn in place of the error. Claude Code runs `StopFailure` rather than `Stop` on such a turn, so `MessageDisplay` is what delivers it. If Claude Code retries once after the stop and the retry answers, that answer stands.

Only the current turn counts. A safeguards stop earlier in the session, a tool call you turned down, a write Holdback already refused: none of those say anything about the reply being checked now. A turn you interrupted, or one that ended in another API error (overloaded, a rate limit, the network, a login), is left alone. Questions about Baton itself are left alone too.

## Running commands

For a task rather than a question, the provider answers with one fenced `bash` block (on Windows it runs in Git Bash, or as a `powershell` block when Git isn't installed). The body runs verbatim as a temp script under `set -e`, in Claude's working directory, so quoting, loops and `python3 -c` work as written. API keys are kept out of that script's environment.

Every simple command has to be on the allow list unless `run_actions.allow` is `"all"`: each part of a chain or pipe, each command in a loop or `if` body, anything in `$(...)`. `max_commands` caps how many there are. A block that fails either check doesn't run at all, since skipping some lines would change what the rest does. The provider is told both limits up front, and a block that still misses them is reported to you rather than run.

What you see is what the script printed. When it printed nothing, you get the provider's own sentence about it with any fenced blocks stripped, and failing that, `Done.`

## Install

```bash
curl -fsSL https://taylorurl.com/baton | sh
```

This installs the `baton` command with pipx or uv (it offers to install uv into `~/.local/bin` if neither is there), removes an older pip install of Baton, adds the bin folder to your PATH in your shell's startup file (`.zshrc`, `.bashrc`, fish's `conf.d`, or `.profile`), then runs `baton setup` and `baton doctor`. Open a new terminal afterward to use `baton`. It never uses sudo.

On Windows, in PowerShell:

```powershell
irm https://taylorurl.com/baton.ps1 | iex
```

The same steps, with the bin folder added to your user PATH. Run `baton setup` with options by passing them on: `& ([scriptblock]::Create((irm https://taylorurl.com/baton.ps1))) --provider local --yes`.

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

## Updates

`baton update` reinstalls Baton from `main` with pipx or uv, whichever installed it. The next Claude Code prompt uses the new version.

## Config

`~/.config/baton/config.json`, or `%APPDATA%\baton\config.json` on Windows. `setup` writes it, and you can edit it by hand.

| Key | Default | |
| :--- | :--- | :--- |
| `enabled` | `true` | Off switch |
| `provider` | `""` | Name of an entry in `providers`. Nothing is sent while it's empty. |
| `providers.<name>` | | `type` (`xai`, `openai`, `anthropic`, `openai_compatible`, `github_issue`), `model`, `api_key_env`, `base_url` |
| `providers.<name>.model` | `"auto"` | `"auto"` is the provider's newest flagship, read from its model list once a day (a local server: the first model it lists). Or a model name. |
| `timeout_seconds` | `300` | How long to wait for the provider |
| `judge.enabled` | `"auto"` | Whether a reply declined is decided by a model reading it against your request. `"auto"` is on when the provider has a key or is local; off, nothing is handed off. |
| `run_actions.enabled` | `true` | Run a `bash` block from the provider's reply in Claude's working directory |
| `run_actions.allow` | `"all"` | `"all"`, or a list of the commands that may run. With a list, a block with any other command doesn't run at all. |
| `run_actions.read_only` | | Set by `--commands read-only`; also refuses writing to files with `>` |
| `run_actions.max_commands`, `run_actions.timeout_seconds` | `20`, `60` | Most commands in one block; how long a block may run |
| `context.user_messages`, `context.tool_calls` | `4`, `10` | How much recent context is sent with a request |

Environment overrides: `BATON=0` (off), `BATON_PROVIDER`, `BATON_MODEL`, `BATON_TIMEOUT`, `BATON_JUDGE=0`, `BATON_RUN_ACTIONS=0|1`, `BATON_CONFIG`, `BATON_STATE`.

`github_issue` is a different sort of provider: it files the task as an issue in a repo you name and waits for a comment starting with `## Handback`, for when a person or another bot answers instead of a model. `setup` doesn't offer it, so set it in the config by hand.

## Privacy

API keys are stored in `~/.config/baton/secrets.env` with mode 600 (`%APPDATA%\baton\secrets.env` on Windows), or read from your environment. Each key is only sent to its own provider.

What leaves your machine on a handoff is the request, your last few messages and recent tool calls, clipped. Logs stay in `~/.local/state/baton` (`%LOCALAPPDATA%\baton` on Windows).

## Uninstall

```bash
baton uninstall --all
```

This removes Baton's hooks and the PATH line the installer added, deletes `~/.config/baton` (config and keys), `~/.local/state/baton` and Baton's backups of `settings.json`, then removes the `baton` command with `pipx uninstall claude-baton` or `uv tool uninstall claude-baton`, whichever installed it. Other hooks in `settings.json` are left alone. On Windows it removes the user PATH entry the installer added, and the `baton` command a few seconds after it exits.

`baton uninstall` on its own only removes the hooks, and `--purge` also deletes the config, keys, logs and backups.

To run the tests from a checkout: `python3 -m unittest`.

Built with Python, standard library only. MIT licensed, see [LICENSE](LICENSE).
