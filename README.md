# skill-hint

**A Hermes plugin that notices which skill the user just asked for — offline, in microseconds, for free.**

Hermes ships a system-prompt index of every installed skill: `name: description`, dozens of lines
long. That index is written in the register of documentation, but people do not talk like
documentation. They say *"merge these two pdfs"*, *"把这份周报做成ppt"*, *"the deploy is broken"*.
The skill that would do the job exists, the model never opens it, and the turn is spent
improvising something worse.

`skill-hint` runs a BM25 query over your skill library on each user turn. When a skill clearly
matches, it appends three lines to the context naming the candidates. When nothing matches, it
adds nothing at all.

```text
[skill-hint] The message may match these skills — load one with skill_view
before acting (auto-matched, may be wrong):
- pdf: PDF files: create, read, merge, fill, OCR, edit text.
- document-to-action-items: Turn a transcript into owner/date/action items.
```

## Why this and not "let the model figure it out"

| | system-prompt index alone | an LLM router | **skill-hint** |
|---|---|---|---|
| extra tokens per turn | 0 | 1 full model call | ~60 |
| latency | 0 | 0.5–3 s | **0.03 ms** |
| needs an API key | no | usually yes | **no** |
| sends your message off-box | no | **yes** | **no** |
| works when the model is in a hurry | ✗ | ✓ | ✓ |

The point is not to be cleverer than the model. It is to put the right three names in front of it
for free, on the turn where they matter.

## Install

```bash
hermes plugins install kosmo888/skill-hint   # from GitHub
# or, from a local checkout:
hermes plugins install /path/to/skill-hint
hermes plugins enable skill-hint
```

Requires `hermes >= 0.22.0`. Python standard library only: no dependencies to resolve, no
virtualenv to poison.

## Configuration

Every setting is exposed through the plugin schema, so it is editable from `hermes config` (or
`config.yaml` under `plugins.entries.skill-hint.settings`). Each also accepts a
`SKILL_HINT_<UPPER_SNAKE_CASE>` environment variable override.

| setting | default | meaning |
|---|---|---|
| `enabled` | `true` | master switch per profile |
| `max_hits` | `3` | candidates per hint — keep it small |
| `max_context_chars` | `600` | hard ceiling for the injected block |
| `min_score` | `3.0` | BM25 floor; raise to be quieter, lower to be more eager |
| `relative_floor` | `0.35` | drop anything below this fraction of the top hit |
| `index_ttl_seconds` | `300` | re-index interval, so new skills appear without a restart |
| `aliases_file` | `""` | path to an `alias: skill-name` map; defaults to `$HERMES_HOME/skill-hint-aliases.yaml` |
| `extra_skill_roots` | `""` | comma-separated extra skill directories |
| `language` | `auto` | `auto` follows the user's message; or force `en` / `zh` |

`min_score` is on the BM25 scale and therefore grows with library size; the plugin scales it down
automatically for libraries under ~150 skills so a fresh install does not go silent.

## Aliases: the reliable escape hatch

Lexical matching cannot know that *"slides"* means your `deck-builder` skill, or that *"流程图"*
lives in a skill whose description says *"结构化图表"*. That is what aliases are for — they beat any
BM25 hit, by design:

```yaml
# $HERMES_HOME/skill-hint-aliases.yaml
slides: ppt-master                  # alias: skill-name
流程图: processon-diagram-generator

xlsx: [表格, spreadsheet]            # skill-name: [aliases]
google-workspace:                   # skill-name: then a block list
  - 日历
  - 日程
```

All three shapes are accepted, because the 0.x release shipped the last two. See
`examples/aliases.example.yaml`. Aliases pointing at skills that no longer exist are dropped
silently, so a rename never breaks the turn.

## What it indexes

`$HERMES_HOME/skills`, `$HERMES_HOME/hermes-agent/skills`, and any `plugins/*/skills`, plus
`extra_skill_roots`. **Symlinked skill directories are followed** — a common layout
(`~/.hermes/skills/foo -> ~/dotfiles/skills/foo`) used to make whole families of skills invisible
to naive scanners, including the one this plugin started as.

Per skill it reads the front-matter `name` and `description` (including `|` and `>` block
scalars) plus the first 1000 characters of the body with fenced code stripped. Name terms are
weighted 3.0, description terms 2.0, body terms 1.0; a candidate must hit the `core` field at
least once, which is what keeps a passing mention buried in prose from generating a hint.

## Measured behaviour

On a real 261-skill library (this author's, mixed English and Chinese), against 17 hand-written
queries whose expected skill was hand-labelled:

```
build:  0.38 s   (once per TTL, single pass)
query:  p50 0.033 ms, p95 0.071 ms
top-1:  88 %   (15/17)      top-3: 94 %   (16/17)
silent on unrelated chatter: 2/3  — "the meeting is at 3pm in room 4" still false-fires on
                                    skills whose names share "meeting"/"room"
```

Reproduce with `python scripts/benchmark.py --skills <dir> [--skills <dir> ...]`.

Known failure modes, stated plainly:

* **A query with two content words can rank the wrong one first.** *"create a ppt from these
  notes"* ranks a notes skill first because it matches both `ppt` and `notes` lexically and the
  library has four competing `ppt*` skills. The right skill is still in the top 3; if you care,
  alias it.
* **Synonyms are invisible.** *"翻译这篇文章"* matches nothing unless some skill's own words overlap.
  No stemming-in-the-linguistic-sense, no embeddings. That is the price of zero dependencies; the
  alias file is the remedy.
* **It is a hint, not a decision.** The injected block says so, and the model is free to ignore it.

## Privacy and side effects

* No network access anywhere in the code. No `urllib`, no `requests`, no sockets.
* No model call, no API key, no environment variables required.
* Reads `SKILL.md` files under the roots above; keeps the index in memory; writes nothing.
* Skill names and descriptions reach the model only when a hint is injected — exactly the
  information the model would have read had it opened the skill itself.

## Tests

```bash
python -m pytest tests -q     # 23 tests, stdlib + pytest only
hermes plugins validate .     # manifest / capability / security admission checks
hermes plugins doctor .       # loads it the way the gateway does and reports the real contract
```

## 中文说明

`skill-hint` 在每轮对话开头对本机技能库做一次本地 BM25 检索，命中时才把 2–3 个候选技能名注入
上下文，让模型知道该 `skill_view` 哪一份；没命中就一句话都不加。

- 纯本地、零依赖、不出网、不调模型、不要 key；单次检索 0.03 ms，注入约 60 字。
- 中文按 2–3 字滑窗分词，能识别「画一张流程图」→ `processon-diagram-generator`。
- 词法检索的边界很明确：同义词、简称靠 `~/.hermes/skill-hint-aliases.yaml` 里的别名兜底，
  别名优先于任何检索结果。
- 符号链接的技能目录会被索引（很多人的技能是软链到 dotfiles 仓库的）。

## License

MIT.
