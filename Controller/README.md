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
