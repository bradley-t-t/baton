# claude-handoff

When [Claude Code](https://docs.anthropic.com/en/docs/claude-code) turns down a request, claude-handoff sends it to another AI you pick (Grok, OpenAI, a local model and so on) and has Claude pass the answer along instead of the refusal.

The answer ends with a tag like `(via Grok)`, so you know where it came from.

<details>
<summary>What it does</summary>

- Spots refusals with a word scan, plus an optional model check for the ones the scan misses
- Sends your request and a bit of recent context to the provider you picked
- Has Claude pass the answer along as is, and never hands off the same reply twice
- Skips replies that failed for a technical reason, replies that got cut off, approvals you said no to, and Holdback blocks

Needs Python 3.9 or later. Standard library only.

</details>

<details>
<summary>How it works</summary>

Two Claude Code hooks, a draft check and a small state directory. One gate in `handoff.py` does the work: `reason()` decides if a reply is a decline, `complete()` gets the answer.

1. **Record.** `UserPromptSubmit` saves the request and adds the draft-check step (below) as `additionalContext`.
2. **Detect.** The draft check runs on Claude's draft before it's sent. `Stop` is the backstop: it reads the final reply from the transcript and skips anything the check already cleared. A first-person refusal regex runs first. The optional judge asks a model yes or no for refusals the regex misses. It's `"auto"` by default, meaning on only when it has a key or the endpoint is local. The judge runs with `CLAUDE_HANDOFF_JUDGE_ACTIVE` set, which turns the handoff off inside it, so it can't recurse.
3. **Filter.** Questions about the tool itself, interrupted replies, approvals you said no to and Holdback blocks are skipped. A reply that failed for a technical reason isn't a decline.
4. **Hand off.** The request goes to the provider with a capped amount of context: the last 4 user messages and the last 10 tool calls, each clipped.
5. **Deliver.** The hook returns `{"decision": "block", "reason": ...}` telling Claude to relay the answer word for word. It saves the expected text and checks Claude's next reply against it. On a mismatch it blocks again, up to 3 times, then lets the stop through.

Two things stop loops. Each `(session, reply)` key goes into a seen set (last 500). And a `stop_hook_active` Stop with nothing pending does nothing.

</details>

<details>
<summary>Install</summary>

```bash
git clone https://github.com/bradley-t-t/claude-handoff.git
cd claude-handoff
python3 -m pip install -e .
claude-handoff install
```

`install` backs up `~/.claude/settings.json`, adds the two hooks, and creates `~/.config/claude-handoff/config.json` from `config.example.json` if you don't have one.

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

API keys go in `~/.config/claude-handoff/secrets.env` (for example `XAI_API_KEY=...`). The hooks load it if it's there.

Then test it:

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

- Clear: prints `claude-handoff: clear. Send this draft word for word.` Tether's check runs next, if it's installed.
- Decline: hands off, runs any allowed commands, and prints `do NOT send your draft` with the exact replacement ending in `(via Grok)`. The refusal never gets sent.
- If Claude skips the check, the Stop hook still catches it.

The UserPromptSubmit hook adds this step as `additionalContext`, same as Tether.

</details>

<details>
<summary>Running commands</summary>

If you asked for a task, not a question, the provider can answer with commands in a fenced `bash` block:

````
```bash
rm /tmp/example.txt
```
````

The block is written to a temp script and run with `bash` in Claude's working directory, so quoting, loops and `python3 -c` work as written. Before anything runs, each command is split with `shlex` and its first word checked against the `run_actions` allow list (one list, or one per provider). One miss rejects the whole block. You get a short result like `Deleted /tmp/example.txt. (via Grok)`.

The `config.example.json` that `install` copies turns this on with a short list of everyday commands. Set `run_actions.enabled` to `false` to turn it off.

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
<summary>Hiding refusals on screen</summary>

Claude Code's `MessageDisplay` hook can replace what is drawn without changing the transcript. claude-handoff uses it the way Ratchet did: a refusal or a narrating relay is replaced with a zero-width space so it never shows; only the final `<result>\n(via Grok)` is drawn. Stop still cannot unshow text that already streamed before the hook ran.

</details>

<details>
<summary>License</summary>

MIT. See [LICENSE](LICENSE).

</details>
