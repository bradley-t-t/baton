# Baton

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) turns down a request, Baton sends it to another AI you pick (Grok, OpenAI, a local model and so on) and shows that answer where the refusal would have been. It ends with a tag like `(via Grok)` so you know where it came from.

## How a refusal becomes an answer

Three hooks: `UserPromptSubmit`, `MessageDisplay` and `Stop`. The first just remembers what you asked. It injects nothing into Claude's context.

`MessageDisplay` sees each reply before it's drawn. A first-person refusal regex runs on it, and an optional judge model catches the refusals the regex misses. The judge is `"auto"` by default, so it's on only when it has a key or the endpoint is local. It runs with `BATON_JUDGE_ACTIVE` set, which turns the handoff off inside it and stops it from recursing. A one-line status before a tool call ("Looking at the file first.") only gets the regex.

On a refusal, the request goes to your provider with a little context: the last 4 user messages and the last 10 tool calls, each clipped. The hook then swaps the refusal on screen for the answer plus `(via Grok)`, and blanks anything Claude says after it in that turn. That only changes what's drawn. The transcript keeps the original. The hook gets a 120-second timeout so a slow provider and its commands can finish.

`Stop` is the backstop. It never blocks, so you don't get "Hook re-prompted Claude" rows. If the display hook missed a refusal, Stop hands it off and the next display draws the result.

Some replies are never handed off: questions about Baton itself, interrupted replies, approvals you said no to, Holdback blocks, and replies that failed for a technical reason. Each handed-off reply is remembered (last 500), so the same one never goes twice.

## Install

```bash
git clone https://github.com/bradley-t-t/baton.git
cd baton
python3 -m pip install -e .
baton install
```

`install` backs up `~/.claude/settings.json`, adds the three hooks, and creates `~/.config/baton/config.json` from `config.example.json` if there isn't one. `baton status` shows whether they're in, and `baton uninstall` takes them out.

Set `"provider"` in that config. API keys go in `~/.config/baton/secrets.env` (`XAI_API_KEY=...`), which the hooks load if it's there. `baton test` sends a test request, and `--provider local` tries a different one.

<details>
<summary>Providers</summary>

| Provider | Talks to | Key |
| :--- | :--- | :--- |
| `xai` | Grok at `https://api.x.ai/v1` | `XAI_API_KEY` |
| `openai` | OpenAI models | `OPENAI_API_KEY` |
| `local` / `lmstudio` | Any OpenAI-compatible server (Ollama, LM Studio, vLLM) | usually none |
| `anthropic` | The Anthropic API | `ANTHROPIC_API_KEY` |
| `queue` | Opens a GitHub issue and waits for a `## Handback` comment | `GITHUB_TOKEN` or `gh auth` |

</details>

<details>
<summary>Letting the provider run commands</summary>

If you asked for a task rather than a question, the provider can answer with a fenced `bash` block. It's written to a temp script and run with `bash` in Claude's working directory, so quoting, loops and `python3 -c` work as written.

Every command in the block has to be on the `run_actions` allow list (one list, or one per provider). That means every part of a chain or pipe, every command in a loop or `if` body, and anything in `$(...)` or backticks, not just the first word on each line. The block can also have at most `run_actions.max_commands` commands (default 20). A loop body counts once.

The block runs as one script, so it's all or nothing. If it's over the limit or has a command that isn't allowed, none of it runs. The provider is told both limits up front. If its block still misses, it gets one more try with the reason, and if that misses too you see why, like `Nothing was run: the script had 8 commands and the limit is 5 (run_actions.max_commands).` When it runs, you see a short result like `Deleted /tmp/example.txt. (via Grok)`.

The `config.example.json` that `install` copies turns this on with a short list of everyday commands. Set `run_actions.enabled` to `false` to turn it off.

</details>

<details>
<summary>Environment variables</summary>

| Variable | What it does |
| :--- | :--- |
| `BATON=0` | Turns it off |
| `BATON_PROVIDER` | Provider for this run |
| `BATON_MODEL` | Model override |
| `BATON_TIMEOUT` | Seconds to wait for an answer (default 300) |
| `BATON_JUDGE=0` | Turns off the judge |
| `BATON_RUN_ACTIONS` | Turns running provider commands on (`1`) or off (`0`) |
| `BATON_CONFIG` | Path to the config file |
| `BATON_STATE` | Logs and state (default `~/.local/state/baton`) |
| `BATON_REPO`, `BATON_TOKEN` | Repo and token for the `queue` provider |

The old `CLAUDE_HANDOFF_*` names still work when the `BATON_*` one isn't set.

</details>

<details>
<summary>Moving from claude-handoff</summary>

Baton used to be called claude-handoff. GitHub sends the old repo URL here, but point your clone at the new one:

```bash
cd ~/Development/claude-handoff   # wherever your clone is
git remote set-url origin https://github.com/bradley-t-t/baton.git
git pull
python3 -m pip uninstall -y claude-handoff
python3 -m pip install -e .
baton install
```

`baton install` swaps the old claude-handoff hooks in `~/.claude/settings.json` for the new ones, so nothing runs twice. It copies `config.json` and `secrets.env` from `~/.config/claude-handoff/` to `~/.config/baton/` and leaves the old files alone. If the copied config still has the old `max_commands` of 5, it's raised to 20. Until you run it, the old config folder, secrets file and `CLAUDE_HANDOFF_*` variables keep working. Logs start fresh in `~/.local/state/baton`.

If a `CLAUDE.md` or Tether setup calls `bin/claude-handoff-check`, change it to `bin/baton-check`. The check now prints `baton: clear.` and `baton: do NOT send your draft` instead of the `claude-handoff:` versions.

</details>

`baton check <file>` (or `bin/baton-check <file>`) judges a saved draft the same way. Tests are `python3 -m unittest discover -s tests -v`. Everything is mocked, so they don't touch the network.

Built with Python, using only the standard library. MIT licensed, see [LICENSE](LICENSE).
