# RimGPT Controller

This controller runs one manually-invoked AI decision cycle against the local RimGPT RimWorld bridge.

Setup:

```bash
cd Controller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure:

```bash
export OPENAI_API_KEY="..."
```

Optional settings:

```bash
export RIMGPT_MODEL="gpt-5.6"
export RIMGPT_BRIDGE_URL="http://127.0.0.1:47831"
```

Development API-spend and context controls:

```bash
export RIMGPT_MAX_INPUT_TOKENS_PER_REQUEST="30000"
export RIMGPT_MAX_MODEL_REQUESTS_PER_CYCLE="8"
export RIMGPT_MAX_ACTIVE_TOOL_GROUPS="3"
export RIMGPT_PROMPT_CACHE_MODE="implicit"
export RIMGPT_COMPACT_THRESHOLD="20000"
export RIMGPT_MAX_COMPACTIONS_PER_CYCLE="1"

# Optional pricing telemetry. Values are USD per million tokens and must be
# updated when the selected model's pricing changes.
export RIMGPT_INPUT_COST_PER_MILLION="..."
export RIMGPT_CACHED_INPUT_COST_PER_MILLION="..."
export RIMGPT_CACHE_WRITE_COST_PER_MILLION="..."
export RIMGPT_OUTPUT_COST_PER_MILLION="..."
```

Before every Responses API request, the controller logs a compact `[CONTEXT]`
breakdown for instructions, dynamic input, tools, state sections, and tool
results. Its input-token estimate is a conservative preflight approximation;
actual usage/cost telemetry is taken from API response usage fields when they
are available. An estimated request above `RIMGPT_MAX_INPUT_TOKENS_PER_REQUEST`
is blocked before it reaches OpenAI. Pricing is optional and does not affect
controller behavior.

First test without executing commands:

```bash
python rimgpt.py --dry-run
```

Run one real decision cycle:

```bash
python rimgpt.py
```

For a deliberately bounded development test sequence:

```bash
python rimgpt.py --test
python rimgpt.py --test --max-cycles 5
```

`--test` defaults to one top-level decision cycle. It can never exceed five
top-level cycles, even if environment configuration requests more. Combine it
with `--dry-run` when model tool proposals should not alter RimWorld:

```bash
python rimgpt.py --test --dry-run
```

The script checks bridge health, retrieves and persists the full authoritative
`/state` locally, sends a compact decision context to the OpenAI Responses API,
executes requested tool calls through the bridge, reports command results,
prints the model's final assessment, and exits. The full snapshot is not sent to
the model.

State/control v2B adds compact spatial context and early colony-building tools:

- `/state` includes a compact `map` overview and bounded `buildings` list.
- `inspect_map(...)` reads a bounded visible map region without putting the whole map in every state snapshot.
- `inspect_map(...)` includes terrain affordances such as `Light`, `Medium`, `Heavy`, and `Bridgeable` where RimWorld exposes them.
- `list_build_options(...)`, `get_build_info(...)`, and `list_growable_plants()` let the model discover valid defNames and build terrain requirements.
- `check_build_placements(...)` validates planned blueprints without placing them.
- Write tools now include stockpile zones, growing zones, construction blueprints, cancellation, and deconstruction designations.

Recommended live checks in RimWorld:

```bash
python rimgpt.py --dry-run
python rimgpt.py
```

For direct bridge checks while a save is loaded:

```bash
curl 'http://127.0.0.1:47831/state'
curl 'http://127.0.0.1:47831/state?afterVersion=1&timeoutMs=2000'
curl 'http://127.0.0.1:47831/map/region?minX=100&minZ=100&maxX=130&maxZ=130'
curl 'http://127.0.0.1:47831/build/options?search=wall'
curl -X POST 'http://127.0.0.1:47831/build/check' -H 'Content-Type: application/json' -d '{"placements":[{"buildDef":"Wall","x":100,"z":100,"rotation":"North","stuffDef":"WoodLog"}]}'
curl 'http://127.0.0.1:47831/growable-plants'
```

Every `/state` response includes `snapshot.version`, `snapshot.capturedAtUtc`, and `snapshot.ticksGame`. After write-command batches, the controller waits for `/state?afterVersion=<oldVersion>` before treating post-action state as authoritative.

## Local StateStore

The controller persists complete authoritative `/state` snapshots locally under
`Controller/state/<colony-key>/`. This runtime directory is intentionally
gitignored. Each colony directory contains:

- `current_state.json`: latest authoritative state.
- `metadata.json`: persistence-format, state-schema, snapshot, map, and lineage metadata.
- `decision_baseline.json`: a separately managed future diff baseline.

RimGPT stores a non-gameplay GUID in its `GameComponent` and exposes it as
`game.colonyLineageId`. It is preserved by normal saves and Save As, while an
unrelated save receives a different value. The controller hashes this ID into a
filesystem-safe colony key, so state and decision baselines cannot cross into a
different colony directory.

Snapshots are written atomically. Invalid JSON, unsupported StateStore formats,
schema changes, or identity mismatches are rejected safely; questionable files
are retained with an `.invalid-...` suffix for diagnosis. `current_state` is
updated from every authoritative bridge fetch. `decision_baseline` is only
advanced after the model reaches a completed final response and the controller
fetches one final authoritative state. Token or request limits, API errors,
incomplete responses, unresolved commands, and ordinary state or command
refreshes leave the old baseline intact.

## Semantic State Deltas

`StateStore.get_changes_since_last_decision()` compares the persisted
`decision_baseline` with `current_state` through `StateDiff`. Reading a delta is
side-effect free and does not advance the baseline. The controller sends this
delta as part of its compact initial decision context; the full current snapshot
remains local.

Normal output contains `fromSnapshot`, `toSnapshot`, `elapsedTicks`, and a
deterministically ordered `changes` object. Stable-ID collections are matched by
ID, work and skill records by `defName`, and power networks by a fingerprint of
their stable connected-building IDs. Missing baselines, colony/map changes,
schema incompatibility, missing identity, or snapshot regression return an
explicit `bootstrapRequired` result instead of comparing unrelated snapshots.

The diff suppresses routine continuous-value noise and emits semantic boundary
crossings. Current bands are:

- Food: urgent below 0.12, low below 0.30.
- Mood: extreme below 0.05, major below 0.18, minor below 0.35.
- Rest: exhausted below 0.14, tired below 0.30.
- Recreation: deprived below 0.15, low below 0.35.
- Bleeding: serious at 0.15, critical at 0.50; pain: moderate at 0.30, severe at 0.60.
- Temperature: dangerous cold below -10 C, freezing below 0 C, cold below 10 C, hot at 30 C, dangerous heat at 40 C.
- Fuel: empty at zero, low below 25 percent; batteries: empty at 2 percent or less, low below 20 percent.
- Research: quarter-cost milestones, completion, and active-project changes.

Pawn and threat movement uses minimum Manhattan distances of 15 and 8 cells;
drafted pawns use 3 cells. Building hit-point changes below 10 percent are
suppressed unless destruction occurs. New threats, drafting, health emergencies,
research completion, equipment changes, and power/fuel state failures remain
explicit.

RimWorld's `IBillGiver` collection includes mobile pawns and animals as well as
stationary worktables. Position-only movement is therefore ignored in the
`operations.worktables` delta; bill and operational-state changes remain
tracked.

Verbose event lists default to 40 details and the complete delta defaults to a
24,000-character cap. Oversized output first aggregates noncritical sections,
then compacts critical colonist/threat details, and finally retains explicit
counts and sample IDs with `detailsTruncated` markers. Truncation is logged.

The current full `/state` schema exposes zone counts/bounds but not complete
zone or allowed-area cell lists, and it does not expose a top-level collection
of individual crop plants. `StateDiff` handles cell geometry and plant events
when those optional fields are present, but otherwise conservatively diffs the
available zone properties, counts, and bounds. It does not infer missing cell or
plant events.

## Strategic Memory

Each colony directory can additionally contain `memory.json`: a small,
human-readable strategic record that is independent of authoritative state and
`decision_baseline.json`. It is loaded only for the same persisted
`game.colonyLineageId`, written atomically, and quarantined if malformed,
unsupported, or copied from another colony. A `/state` schema change still
invalidates the decision baseline, but does not erase compatible strategic
memory.

The current versioned memory schema is:

```json
{
  "version": 1,
  "colonyIdentity": {"kind": "rimGPTGameComponentGuid", "colonyLineageId": "..."},
  "currentGoals": [],
  "nextPriorities": [],
  "longTermGoals": [],
  "decisions": [],
  "unresolvedProblems": [],
  "importantLocations": [],
  "pawnRoles": {},
  "lastAssessment": ""
}
```

`StateStore.apply_memory_update(...)` accepts an additive patch containing text
lists, `decisionsToRemember`, explicit `resolvedDecisions` and
`resolvedProblems`, structured locations, stable-ID pawn roles, and an
`assessment` replacement. Text is normalized for deterministic exact/near-exact
deduplication; no embeddings or model calls are used.

Default bounds are 8 current goals, 10 next priorities, 8 long-term goals, 24
decisions, 15 unresolved problems, 12 locations, 24 pawn-role entries with 4
roles each, 240 characters per item, 600 characters for the assessment, and
9,000 serialized characters overall. Newer items win when a list is full.
`[MEMORY]` telemetry logs serialized characters, estimated tokens, field counts,
and any bounding event.

On every newer authoritative snapshot, StateStore conservatively reconciles
only facts that state proves obsolete: strategic roles for missing colonists,
no/need/build research-bench entries when a completed research bench exists,
colonist-weapon entries when every current colonist has a primary weapon, and a
small explicit set of rice/potato/corn/cotton/healroot growing-zone goals when
the matching nonempty zone exists. Vague plans such as refrigeration or power
are never inferred complete. Memory is included in the compact model context,
with instructions that current live state always overrides it. Model-driven
memory write-back remains deferred.

## Compact Decision Context

The initial dynamic input contains `strategicMemory`, a deterministic
`currentSummary`, `changesSinceLastDecision`, and a small `trigger`. When no
compatible decision baseline exists, it additionally contains a bounded
`bootstrapState` with key pawn capabilities, strategic resources, research,
visible threats, important structures, zones, power, and a compact map overview.
It never falls back to the raw `/state` payload.

The read-only `get_colony_state(section)` model tool retrieves one bounded view
from the latest authoritative `StateStore` snapshot. Allowed sections are
`pawns`, `work`, `resources`, `research`, `buildings`, `zones`, `equipment`,
`apparel`, `beds`, `worktables`, `bills`, `power`, `threats`, and `environment`.
Each result has a snapshot version and truncation marker, is capped at 12,000
serialized characters by default, and cannot traverse arbitrary state paths.

`[CONTEXT]` telemetry reports memory, summary, delta, trigger, bootstrap, and
full-state diagnostic sizes separately. `fullStateSent=false` is explicit on
every model request.

## Model Tool Results

Bridge and controller results remain unchanged for HTTP clients and local
diagnostics. Before a result is returned to the model,
`ModelToolResultFormatter` creates a deterministic bounded view:

- Successful commands contain only the semantic fields changed by the command.
- Completed command IDs, timing, status history, and original command echoes are
  retained internally but omitted from the model result.
- Blueprint and validator batches summarize successes and list only failures.
- Zone results report accepted counts and exceptional cells instead of echoing
  every successful cell.
- Catalogs retain command def names, costs, requirements, and availability, but
  are bounded to 12,000 serialized characters with explicit truncation.
- `inspect_map` deduplicates terrain properties into a palette. Each row contains
  runs encoded as `[xStart,length,terrainPaletteId]`; non-plant entities remain
  explicit and plants are grouped by identical strategic properties with exact
  `[x,z]` cells. Buildability, terrain affordances, zone eligibility, roofs,
  obstacles, stable entity IDs, and coordinates remain available.

If exact terrain encoding cannot fit safely, the formatter asks for a smaller
region instead of returning incomplete geometry. Other oversized results retain
failures first and set `truncated=true`. Formatter failures produce a bounded
conservative failure result and never fabricate success.

Each tool logs a size-only line:

```text
[TOOL RESULT] tool=... rawChars=... modelChars=... compressionRatio=... success=...
```

Per-request `[CONTEXT]` logs include `toolResultCharsThisRound` and
`toolResultCharsAccumulated`. Raw results are retained in a bounded in-memory
diagnostic list for the current cycle and are never dumped into normal logs.

## Capability-Scoped Tools

The controller keeps the complete gameplay tool registry locally, but sends
only the active capability schemas on each Responses request. The always-on
`core` group contains selective colony state, bounded map inspection, speed
control, `list_capabilities`, and `enable_capability`. Additional groups cover
work, construction, zones, production, equipment, power, research, combat, and
utility operations.

`list_capabilities` returns compact group descriptions without embedding tool
schemas. `enable_capability(name)` enables one explicit allowlisted group for
the rest of the current decision cycle; the following Responses continuation
includes that group's schemas. Dynamic groups reset before the next top-level
cycle. By default, at most three non-core groups can be active, controlled by
`RIMGPT_MAX_ACTIVE_TOOL_GROUPS` or `--max-active-tool-groups`. Wildcards and
enable-all requests are rejected, and a tool absent from the active surface
cannot execute even if a model fabricates its function name.

Visible active threats deterministically preload combat. Stable colonies begin
with core only. `[TOOLS]` logs report active groups, tool count, and exact
serialized schema characters for every request.

OpenAI SDK 3.8.0 was inspected for native Tool Search support. Its installed
Python types do not expose `tool_search`, `ToolSearch`, or `defer_loading`, so
RimGPT uses the local registry for predictable testing and debugging. Active
schemas are supplied explicitly on every Responses continuation.

## Prompt Cache and Continuation

The static model policy is a byte-stable prompt prefix versioned as
`context-memory-v1-m8`. Dynamic colony memory, summaries, deltas, triggers,
tool results, and active capability schemas are supplied separately. The
prompt-cache identity is `rimgpt:<prompt-version>:<model>` and never includes a
colony ID, snapshot version, state hash, or secret.

OpenAI SDK 3.8.0 supports `prompt_cache_key`, `prompt_cache_options`, cache-read
and cache-write usage, and `responses.compact`. RimGPT uses implicit prompt
caching with the SDK's supported `30m` TTL for GPT-5.6+ by default. Although
the SDK exposes explicit mode, explicit breakpoints are not added;
deterministic instructions and tool ordering keep the request architecture
simple. Set `RIMGPT_PROMPT_CACHE_MODE=disabled` to omit cache configuration.

Within one decision cycle, each tool continuation supplies the latest
`previous_response_id`, only the new tool outputs and current compact state
update, and the current active tool schemas. The next top-level cycle starts a
fresh Responses request with no previous response ID. Responses conversation
state is never persisted to StateStore or strategic memory.

Native compaction is an emergency within-cycle optimization. It is attempted
at 20,000 estimated input tokens by default, at most once per cycle. The
returned compaction items remain opaque and replace the prior continuation
chain for the next request. The compacted request is re-estimated, and the
30,000-token hard guard still blocks it if necessary. A failed or unsupported
compaction never fabricates context; normal continuation may proceed only when
it remains under the hard limit.

`[PROMPT]`, `[CACHE]`, `[COMPACTION]`, and `[COST]` telemetry distinguishes
stable-prefix size, cache eligibility, actual cached/cache-write tokens, and
local estimates. Local tests cannot prove a cache hit. Prompt caching affects
cost and latency only; StateStore, StateDiff, strategic memory, and the
decision baseline remain authoritative across cycles.

Manual paid cache validation is intentionally separate from development
testing. With RimWorld running in the background, run at most two bounded
dry-run cycles within the cache window:

```bash
python rimgpt.py --test --max-cycles 1 --dry-run
python rimgpt.py --test --max-cycles 1 --dry-run
```

Compare `[COST]` and `[CACHE]` lines for `inputTokens`, `cachedInputTokens`,
`cacheWriteTokens`, `uncachedInputTokens`, output tokens, request/cycle cost,
and hit ratio. A cache hit is not guaranteed. Remove `--dry-run` only when
gameplay mutations are explicitly intended.
