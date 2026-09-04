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

First test without executing commands:

```bash
python rimgpt.py --dry-run
```

Run one real decision cycle:

```bash
python rimgpt.py
```

The script checks bridge health, retrieves `/state`, sends the state to the OpenAI Responses API with the current RimGPT function tools, executes requested tool calls through the bridge, reports command results, prints the model's final assessment, and exits.

State/control v2B adds compact spatial context and early colony-building tools:

- `/state` includes a compact `map` overview and bounded `buildings` list.
- `inspect_map(...)` reads a bounded visible map region without putting the whole map in every state snapshot.
- `list_build_options(...)`, `get_build_info(...)`, and `list_growable_plants()` let the model discover valid defNames.
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
curl 'http://127.0.0.1:47831/growable-plants'
```

Every `/state` response includes `snapshot.version`, `snapshot.capturedAtUtc`, and `snapshot.ticksGame`. After write-command batches, the controller waits for `/state?afterVersion=<oldVersion>` before treating post-action state as authoritative.
