# claude-handoff

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) turns down a request, claude-handoff sends your original request to another AI you pick (Grok, OpenAI, a local model, and so on), waits for the answer, and has Claude pass it along in place of the refusal.

The answer ends with a short tag like `(via Grok)`, so you always know where it came from.

Python 3.9 or later, standard library only.

## Install

```bash
git clone https://github.com/bradley-t-t/claude-handoff.git
cd claude-handoff
python3 -m pip install -e .
claude-handoff install
```

`install` adds two hooks to `~/.claude/settings.json` (after backing it up) and creates `~/.config/claude-handoff/config.json` from `config.example.json` if you don't have one yet.

```bash
claude-handoff status      # are the hooks installed?
claude-handoff uninstall   # take them out
```

## Pick a provider

Edit `~/.config/claude-handoff/config.json` and set `"provider"` to one of these:

| Provider | Talks to | Key |
| :--- | :--- | :--- |
| `xai` | Grok at `https://api.x.ai/v1` | `XAI_API_KEY` |
| `openai` | OpenAI models | `OPENAI_API_KEY` |
| `local` / `lmstudio` | Any OpenAI-compatible server (Ollama, LM Studio, vLLM) | usually none |
| `anthropic` | The Anthropic API | `ANTHROPIC_API_KEY` |
| `queue` | Opens a GitHub issue and waits for a `## Handback` comment | `GITHUB_TOKEN` or `gh auth` |

Put API keys in `~/.config/claude-handoff/secrets.env` (for example `XAI_API_KEY=...`). The hooks load that file when it's there.

Then check it works:

```bash
claude-handoff test
claude-handoff test --provider local
```

## Settings

| Variable | What it does |
| :--- | :--- |
| `CLAUDE_HANDOFF=0` | Turns it off |
| `CLAUDE_HANDOFF_PROVIDER` | Picks a provider for this run |
| `CLAUDE_HANDOFF_MODEL` | Overrides the model |
| `CLAUDE_HANDOFF_TIMEOUT` | How long to wait for an answer, in seconds (default 300) |
| `CLAUDE_HANDOFF_JUDGE=0` | Turns off the extra refusal check |
| `CLAUDE_HANDOFF_CONFIG` | Path to the config file |
| `CLAUDE_HANDOFF_STATE` | Where logs and state go (default `~/.local/state/claude-handoff`) |

## How it works

1. A hook saves your request, and another reads Claude's reply when it finishes.
2. If the reply is a refusal, your request and a bit of recent context go to your provider. A word scan spots most refusals, and an optional quick model check catches the rest.
3. The provider's answer comes back with its `(via ...)` tag, and Claude is asked to pass it along as is. The same reply is never handed off twice.

It skips replies that failed for a technical reason ("pytest isn't installed"), replies that got cut off, approvals you said no to, and questions about the handoff tool itself.

## Running commands

If the request was a task rather than a question, the provider can answer with commands in a fenced `bash` block:

````
```bash
rm /tmp/example.txt
```
````

This is off by default. Turn on `run_actions` in the config and list the commands it's allowed to run (for every provider, or per provider). The block runs as a bash script in the session's working folder, and every command in it has to be on the list or none of it runs. You get a short result like `Deleted /tmp/example.txt. (via Grok)`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Everything is mocked, so no network is needed.

## License

MIT
