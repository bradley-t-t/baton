# claude-handoff

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) turns down a request, claude-handoff sends it to another AI you pick (Grok, OpenAI, a local model and so on) and shows that answer where the refusal would have been. It ends with a tag like `(via Grok)` so you know where it came from.

## How a refusal becomes an answer

Three hooks: `UserPromptSubmit`, `MessageDisplay` and `Stop`. The first just remembers what you asked. It injects nothing into Claude's context.

`MessageDisplay` sees each reply before it's drawn. A first-person refusal regex runs on it, and an optional judge model catches the refusals the regex misses. The judge is `"auto"` by default, so it's on only when it has a key or the endpoint is local. It runs with `CLAUDE_HANDOFF_JUDGE_ACTIVE` set, which turns the handoff off inside it and stops it from recursing. A one-line status before a tool call ("Looking at the file first.") only gets the regex.

On a refusal, the request goes to your provider with a little context: the last 4 user messages and the last 10 tool calls, each clipped. The hook then swaps the refusal on screen for the answer plus `(via Grok)`, and blanks anything Claude says after it in that turn. That only changes what's drawn. The transcript keeps the original. The hook gets a 120-second timeout so a slow provider and its commands can finish.

`Stop` is the backstop. It never blocks, so you don't get "Hook re-prompted Claude" rows. If the display hook missed a refusal, Stop hands it off and the next display draws the result.

Some replies are never handed off: questions about claude-handoff itself, interrupted replies, approvals you said no to, Holdback blocks, and replies that failed for a technical reason. Each handed-off reply is remembered (last 500), so the same one never goes twice.

## Install

```bash
git clone https://github.com/bradley-t-t/claude-handoff.git
cd claude-handoff
python3 -m pip install -e .
claude-handoff install
```

`install` backs up `~/.claude/settings.json`, adds the three hooks, and creates `~/.config/claude-handoff/config.json` from `config.example.json` if there isn't one. `claude-handoff status` shows whether they're in, and `claude-handoff uninstall` takes them out.

Set `"provider"` in that config. API keys go in `~/.config/claude-handoff/secrets.env` (`XAI_API_KEY=...`), which the hooks load if it's there. `claude-handoff test` sends a test request, and `--provider local` tries a different one.

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

Every command in the block is split with `shlex` first, and its first word has to be on the `run_actions` allow list (one list, or one per provider). One miss rejects the whole block. What you see is a short result like `Deleted /tmp/example.txt. (via Grok)`.

The `config.example.json` that `install` copies turns this on with a short list of everyday commands. Set `run_actions.enabled` to `false` to turn it off.

</details>

<details>
<summary>Environment variables</summary>

| Variable | What it does |
| :--- | :--- |
| `CLAUDE_HANDOFF=0` | Turns it off |
| `CLAUDE_HANDOFF_PROVIDER` | Provider for this run |
| `CLAUDE_HANDOFF_MODEL` | Model override |
| `CLAUDE_HANDOFF_TIMEOUT` | Seconds to wait for an answer (default 300) |
| `CLAUDE_HANDOFF_JUDGE=0` | Turns off the judge |
| `CLAUDE_HANDOFF_CONFIG` | Path to the config file |
| `CLAUDE_HANDOFF_STATE` | Logs and state (default `~/.local/state/claude-handoff`) |

</details>

`claude-handoff check <file>` judges a saved draft the same way. Tests are `python3 -m unittest discover -s tests -v`. Everything is mocked, so they don't touch the network.

Built with Python, using only the standard library. MIT licensed, see [LICENSE](LICENSE).
