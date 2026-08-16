# Prompts

Every other file in this directory is a prompt, and **the whole file is sent verbatim**.
There is no header, no front matter, and no comment syntax that gets stripped first —
anything written here reaches the model, so this README exists to say so somewhere the model
will never read.

## Editing one

You may. That is why they are files (**D18**). Two things follow.

**A run records a hash of the prompt it actually sent.** `prompt_version` is a label a human
maintains; the hash is the fact. Two snapshots produced under different prompt texts refuse
to be compared even when both claim `extract-v1`, and say the prompt is why. Editing a prompt
does not corrupt anything — it makes earlier analyses incomparable with later ones, which is
true, and the point is that it is *said* rather than discovered.

**Bump `PROMPT_VERSION` when you change one meaningfully.** The hash catches every edit
including a stray space; the version is how a person describes the change to another person.
They answer different questions, which is why both are recorded.

## Why the lines are long

A paragraph is one line, because the file's bytes are the prompt's bytes. Re-wrapping is a
real edit to the text sent, and will change the hash — allowed, but do it deliberately and
bump the version with it, rather than as a side effect of a formatter.
