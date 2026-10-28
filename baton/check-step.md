# Check every reply with baton first

- Before every reply, write the draft to a scratch file and run `{check} <file>` (Bash timeout 600000). Run this **before** Tether's check. Skipping the check is not allowed for any reason.
- If it prints `baton: clear. Send this draft word for word.`, continue to Tether's check on that same draft as already instructed, then send what Tether clears.
- If it prints `baton: do NOT send your draft`, send ONLY the text that follows, word for word. Do not run Tether's check on the refused draft.
- Write no user-visible text before that final reply.
