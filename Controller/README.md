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

# Optional pricing telemetry. Values are USD per million tokens and must be
# updated when the selected model's pricing changes.
export RIMGPT_INPUT_COST_PER_MILLION="..."
export RIMGPT_CACHED_INPUT_COST_PER_MILLION="..."
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

The script checks bridge health, retrieves `/state`, sends the state to the OpenAI Responses API with the current RimGPT function tools, executes requested tool calls through the bridge, reports command results, prints the model's final assessment, and exits.

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
advanced after a normally completed model decision cycle, never on ordinary
state refreshes.

## Semantic State Deltas

`StateStore.get_changes_since_last_decision()` compares the persisted
`decision_baseline` with `current_state` through `StateDiff`. Reading a delta is
side-effect free and does not advance the baseline. The controller currently
measures and logs these deltas locally, but still sends the complete state to
the model; delta-only model context is intentionally deferred to a later
milestone.

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
