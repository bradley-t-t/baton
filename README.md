# claude-handoff

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) turns down a request, claude-handoff sends your original request to another AI you pick (Grok, OpenAI, a local model, and so on), waits for the answer, and has Claude pass it along in place of the refusal.

It's for people who use Claude Code every day and would rather get an answer from somewhere else than a refusal. The answer ends with a short tag like `(via Grok)`, so you always know where it came from.

<details>
<summary>What it does</summary>

- Spots refusals with a word scan, plus an optional quick model check for the ones the scan misses
- Sends your request and a bit of recent context to the provider you picked
- Has Claude pass the answer along as is, and never hands off the same reply twice
- Skips replies that failed for a technical reason, replies that got cut off, approvals you said no to, and Holdback blocks

You need Python 3.9 or later. It uses the standard library only.

</details>

<details>
<summary>How it works</summary>

Two Claude Code hooks, a draft check, and a small state directory. Everything goes through one gate in `handoff.py`: `reason()` decides whether a reply is a decline, `complete()` gets the answer.

1. **Record.** `UserPromptSubmit` stores the request and injects the draft-check step (below) as `additionalContext`.
2. **Detect.** The draft check runs the same detection on Claude's draft before it is sent. `Stop` is the backstop: it reads the final reply from the transcript and skips any reply the check already cleared. A first-person refusal regex runs first. The optional judge (`"auto"` by default: on only when a key for it is present, or the endpoint is local) asks a model a yes/no question for refusals the regex misses. The judge runs with `CLAUDE_HANDOFF_JUDGE_ACTIVE` set, which disables the handoff inside it, so it can't recurse.
3. **Filter.** Questions about the tool itself, interrupted replies, approvals you declined and Holdback blocks return early. A refusal caused by a technical failure is not a decline.
4. **Hand off.** The request goes to the provider with bounded context: the last 4 user messages and the last 10 tool calls, each clipped.
5. **Deliver.** The hook returns `{"decision": "block", "reason": ...}` with an instruction to relay the answer verbatim. It stores the expected text and checks Claude's next reply against it, re-blocking up to 3 times on a mismatch before letting the stop through.

Loops are closed two ways: each `(session, reply)` key goes into a seen set (last 500), and a `stop_hook_active` Stop with no pending expectation does nothing.

</details>

<details>
<summary>Install</summary>

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

</details>

<details>
<summary>Pick a provider</summary>

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

</details>

<details>
<summary>Settings</summary>

| Variable | What it does |
| :--- | :--- |
| `CLAUDE_HANDOFF=0` | Turns it off |
| `CLAUDE_HANDOFF_PROVIDER` | Picks a provider for this run |
| `CLAUDE_HANDOFF_MODEL` | Overrides the model |
| `CLAUDE_HANDOFF_TIMEOUT` | How long to wait for an answer, in seconds (default 300) |
| `CLAUDE_HANDOFF_JUDGE=0` | Turns off the extra refusal check |
| `CLAUDE_HANDOFF_CONFIG` | Path to the config file |
| `CLAUDE_HANDOFF_STATE` | Where logs and state go (default `~/.local/state/claude-handoff`) |

</details>

<details>
<summary>Draft check (before send)</summary>

Before every reply, Claude writes the draft to a scratch file and runs:

```bash
bin/claude-handoff-check <file>
# or: python3 -m claude_handoff.cli check <file>
```

- Clear → `claude-handoff: clear. Send this draft word for word.` (then Tether's check, if installed).
- Decline → hands off, runs allowed actions, prints `do NOT send your draft` plus the exact replacement ending in `(via Grok)`. The refusal never becomes the sent reply.
- The Stop hook remains a backstop if Claude skips the check.

The UserPromptSubmit hook injects this step as `additionalContext` (same pattern as Tether).


</details>

<details>
<summary>Running commands</summary>

If you asked for a task rather than a question, the provider can answer with commands in a fenced `bash` block:

````
```bash
rm /tmp/example.txt
```
````

The fence body is written to a temp script and run with `bash` in Claude's working directory, so quoting, loops and `python3 -c` survive intact. Before anything runs, each simple command is split with `shlex` and its first token checked against the `run_actions` allow list (flat, or per provider). One miss rejects the whole block. You get a short result like `Deleted /tmp/example.txt. (via Grok)`. The `config.example.json` that `install` copies turns this on with a short list of everyday commands. Set `run_actions.enabled` to `false` to turn it off.

</details>

<details>
<summary>Tests</summary>

```bash
python3 -m unittest discover -s tests -v
```

Everything is mocked, so no network is needed.

</details>

Built with Python, using only the standard library.

<details>
<summary>License</summary>

MIT. See [LICENSE](LICENSE).

</details>
