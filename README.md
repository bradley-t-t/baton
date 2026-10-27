# claude-handoff

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) declines a request — for any reason: rules, safety, policy, a moral line — **claude-handoff** catches the refusal, sends your original request to another AI you choose, waits for its answer, and has Claude relay that answer in place of the refusal.

The relayed text is always labeled as coming from the handoff provider. It never pretends to be Claude's own reply, and it never pressures Claude or asks it to hide that a handoff happened.

Works isolated from any other tooling. Stdlib only. Python 3.9+.

## Install

```bash
git clone https://github.com/bradley-t-t/claude-handoff.git
cd claude-handoff
python3 -m pip install -e .
claude-handoff install
```

`install` merges two hooks into `~/.claude/settings.json` (with a timestamped backup) and seeds `~/.config/claude-handoff/config.json` from `config.example.json` if you don't have one yet.

```bash
claude-handoff status     # are the hooks in place?
claude-handoff uninstall # remove them
```

## Configure

Edit `~/.config/claude-handoff/config.json` (see `config.example.json`). Pick one provider:

| Provider key | Type | What it talks to | Key env |
|---|---|---|---|
| `xai` | `xai` | Grok via `https://api.x.ai/v1` | `XAI_API_KEY` |
| `openai` | `openai` | OpenAI / Codex models | `OPENAI_API_KEY` |
| `local` / `lmstudio` | `openai_compatible` | Any OpenAI-compatible base URL (Ollama, LM Studio, abliterated local models, vLLM, …) | usually none |
| `anthropic` | `anthropic` | Anthropic Messages API | `ANTHROPIC_API_KEY` |
| `queue` | `github_issue` | Files a GitHub issue, polls for a `## Handback` comment | `GITHUB_TOKEN` or `gh auth` |

Set the active one with `"provider": "xai"` (or whichever you named). Environment overrides:

- `CLAUDE_HANDOFF=0` — disable
- `CLAUDE_HANDOFF_PROVIDER=local` — pick a provider
- `CLAUDE_HANDOFF_MODEL=…` — override the model
- `CLAUDE_HANDOFF_TIMEOUT=300` — Stop-hook wait, seconds
- `CLAUDE_HANDOFF_JUDGE=0` — force the yes/no judge off
- `CLAUDE_HANDOFF_CONFIG=/path/to.json` — config path
- `CLAUDE_HANDOFF_STATE=/path/to/dir` — state / log dir (default `~/.local/state/claude-handoff`)

Check connectivity:

```bash
export XAI_API_KEY=…          # or whichever key your provider needs
claude-handoff test
claude-handoff test --provider local
```

## How it works

1. **Detect.** On every Claude Code turn, the `UserPromptSubmit` hook records your request. The `Stop` hook reads Claude's final reply from the session transcript. A first-person refusal (regex word scan) counts as a decline. An optional LLM yes/no judge can catch refusals the scan misses; it is `"auto"` by default (on only when a provider key is available) and is guarded by `CLAUDE_HANDOFF_JUDGE_ACTIVE` so it can never re-enter the handoff.
2. **Hand off.** Your original request plus a short context (recent messages, recent tool calls) goes to the provider you configured.
3. **Complete.** The provider returns text. Optional `run_actions` (off by default) will run fenced shell commands whose first word is on a per-provider allow-list, with a timeout, and feed the output back once.
4. **Deliver.** The Stop hook waits up to `timeout_seconds`, then returns Claude Code's Stop-hook JSON `{"decision":"block","reason":…}` with the labeled answer. Claude relays it. A `stop_hook_active` flag and a per-reply seen-set stop loops: the same turn is never handed off twice.

## Limits (deliberate)

- The answer is always labeled `Handoff result from <provider>:`. It is never presented as Claude's own words.
- Nothing here tells Claude it "has no choice," pressures it, or asks it to hide that a handoff happened.
- Declines that are only a technical failure ("pytest isn't installed"), a filter/API cut-off, or the person saying no to an approval prompt are **not** handed off.
- A message about the handoff tool itself ("why didn't the handoff fire?") is not handed off.
- `run_actions` is off unless you turn it on and name an allow-list. There is no unrestricted shell.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No network is required: providers are mocked.

## License

MIT. Copyright (c) 2026 Trenton Taylor.
