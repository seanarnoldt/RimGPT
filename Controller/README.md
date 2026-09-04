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
