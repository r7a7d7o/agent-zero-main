# Agent Zero Model Efficiency Scorecard

Date: 2026-05-10

This scorecard synthesizes the untracked `EVIDENCE-*.md` provider sweeps. It is not a general intelligence benchmark. It scores how efficiently each provider/model pairing drove Agent Zero's live tool suite under short, median-user prompts.

## Scoring

Score is a 0-100 review score based on:

- first-pass correct tool routing
- successful completion without repair turns
- local/server vs host/remote locality discipline
- correct use of skill-gated tools
- provider support for vision and remote/CUA surfaces
- low final-answer drift, repeated tool loops, and hallucinated success

Repairs, partial routing, provider-specific limitations, unnecessary tool probes, and cleanup hazards lower the score even when the final user-visible answer was correct.

## Chart

| Rank | Provider / model | Score | Chart | Main read |
|---:|---|---:|---|---|
| 1 | Nebius AI / `Qwen/Qwen3.5-397B-A17B` | 96 | `###################-` | Best broad reliability; most tools passed, with repair noise mostly confined to patch/scheduler. |
| 2 | A0 Venice / `z-ai-glm-5v-turbo` | 92 | `##################--` | Strong unified-tool behavior; Venice image validation and patch repair remain the main costs. |
| 3 | OpenRouter / `anthropic/claude-sonnet-4.6` | 89 | `##################--` | Cleanest locality and patching; remaining gaps are higher-level routing and semantic scope. |
| 4 | OpenRouter / `xiaomi/mimo-v2.5-pro` | 88 | `##################--` | Strong explicit-tool performer; natural remote patch and unsupported vision endpoint hurt reliability. |
| 5 | Moonshot AI / `kimi-2.6` | 87 | `#################---` | Good primitives and scheduler; skill read_file, document query routing, and vision perception were weaker. |
| 6 | OpenRouter / `google/gemini-3.1-flash-lite` | 85 | `#################---` | Strong tool surface, but behavior/memory persistence leaked temporary rules into later chats. |
| 7 | A0 Venice / `google-gemma-4-26b-a4b-it` | 83 | `#################---` | Good for a smaller open model after prompt cleanup; memory wording, scheduler, and Venice vision were fragile. |
| 8 | OpenRouter / `anthropic/claude-haiku-4.5` | 81 | `################----` | Usable when explicit; natural memory, scheduler shape, CUA status, and notify defaults are less reliable. |
| 9 | SambaNova / `MiniMax-M2.7` | 71 | `##############------` | Concrete tools often worked, but provider noise, natural memory, notify loops, CUA, and vision were risky. |
| 10 | OpenRouter / `openai/gpt-4.1-mini` | 67 | `#############-------` | Basic syntax is competent, but natural routing often looks successful while using the wrong tool path. |
| 11 | Nebius AI / `nvidia/Nemotron-3-Nano-Omni` | 66 | `#############-------` | Most affected by stale final answers, local/remote confusion, memory misses, and patch instability. |

## Cross-Provider Failure Clusters

- Natural document questions often use `text_editor` instead of `document_query`.
- Skill requests sometimes jump straight to `load` and skip `search`, even when the user asks to find a skill.
- Scheduler reminders often start with ISO timestamps in `schedule` instead of cron fields or `plan`.
- Normal notifications often omit `priority: 10` or use `success` styling for plain notes.
- Patch requests are still cognitively expensive for smaller models, especially after locality ambiguity.
- Natural memory requests conflict with prompt-include guidance unless the prompt separates durable memory from project instruction files.
- Behavior adjustments can leave vector-memory residue even when `behaviour.md` is restored.
- Vision reliability is provider-specific; some endpoints reject image input or validate embedded media differently.
- CUA should stay skill-gated and beta; status is generally safer than capture/action testing.

## Improvements Applied From This Pass

- Renamed high-impact skills to task-oriented names and moved plugin-owned skills into their plugin folders.
- Updated skill frontmatter on renamed skills toward the official `name` + `description` standard.
- Clarified memory-vs-promptinclude guidance so "remember/forget" routes to memory tools.
- Clarified scheduler one-time vs cron task shapes and timezone handling.
- Clarified `document_query` as the preferred document-QA tool for document paths and URLs.
- Clarified `skills_tool` search/load/read_file order and required arguments.
- Clarified normal notification priority/type.
- Clarified local and host text-editor patch guidance when the user says not to rewrite.

## Next Candidates

- Make memory delete/forget clean up derived fragments more predictably, or expose fragment cleanup as an explicit backend result.
- Add behavior-adjustment scoping and cleanup protection so temporary rules do not become persistent vector memories.
- Consider a scheduler convenience path for one-time reminders that maps ordinary time phrases to `create_planned_task` without relying on model cron synthesis.
- Add provider capability checks before `vision_load` injects image content into text-only or image-rejecting endpoints.
- Improve patch ergonomics with a simpler replace-by-exact-text path for both local and host file editors.
- Add notify de-duplication/loop protection at the tool layer for repeated identical notifications.
- Improve A2A error text around required response history so models do not treat `(no response)` as success.
