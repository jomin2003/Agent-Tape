# Examples

Five runnable scripts. **None of them touch the network** — the models and tools
are local mocks, so you can run them right now, offline, for free. Each one writes
its tape into a temporary directory and cleans up after itself.

```bash
# from the repository root
PYTHONPATH=src python examples/01_minimal_record_and_replay.py
PYTHONPATH=src python examples/02_tool_using_agent.py
PYTHONPATH=src python examples/03_catching_divergence.py
PYTHONPATH=src python examples/04_determinism_check.py
PYTHONPATH=src python examples/05_rollback_and_fork.py
```

Or, if you installed with `pip install -e .`, drop the `PYTHONPATH=src`.

| # | Script | What it shows |
|---|---|---|
| 01 | `01_minimal_record_and_replay.py` | The smallest useful thing: record a two-step run, replay it, and assert that the model and the tool were never invoked the second time. Also prints the tape. |
| 02 | `02_tool_using_agent.py` | A realistic plan/act/observe loop using the clock, the RNG, tools and state snapshots. Includes a 20× replay timing loop. |
| 03 | `03_catching_divergence.py` | Replay as a regression test. Breaks the agent in three different ways and shows the three failure modes replay reports. |
| 04 | `04_determinism_check.py` | The tape self-test, plus what a *leak* looks like — an agent reading raw entropy behind the library's back. |
| 05 | `05_rollback_and_fork.py` | Recorded compensation plans, replaying a rollback without re-issuing refunds, and counterfactual replay via forking. |

## A note on what these examples deliberately do

They use mocks, which makes them fast and hermetic, but it would be easy to read
them and conclude that `agenttape` is about mock testing. It is not.

Swap `call_model` for a real provider client and `search_index` for a real index
and nothing else in the example changes — that is the point of the design. The
library records the *boundary*, not the implementation behind it.

What the examples cannot show you, because it needs a real model, is the thing
that motivates the whole library: that the same prompt, sent twice to the same
endpoint at `temperature=0`, does not reliably produce the same tokens. See
[`../research/deterministic-replay-and-rollback-for-ai-agents.md`](../research/deterministic-replay-and-rollback-for-ai-agents.md)
for the evidence.
