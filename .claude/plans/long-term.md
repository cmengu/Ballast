Week 1 — foundations

New repo: ballast
Read AG-UI event spec end to end, get a LangGraph agent streaming AG-UI events locally
Define AgentStream base class — the abstraction both AG-UI and TinyFish adapters implement
Port memory layer from GroundWire — it's already mostly transport-independent, just needs confidence decay added
pip install ballast works locally by end of week

Week 2 — core primitives

spec.py — intent grounding layer, goal specificity scoring, targeted clarification, locked spec before execution starts
trajectory.py — validator ported to operate against locked spec not raw goal string, per-domain threshold calibration
guardrails.py — port from GroundWire, already clean
Both AG-UI and TinyFish adapters wired up, tests passing on both

Week 3 — the feature TinyFish couldn't do

True pause/inject/resume using AG-UI native intervention points
Healer ported — hypothesis → sandbox → confirmed fix, now operating against spec
Replan loop detection — same drift post-replan escalates instead of looping
This is the centrepiece feature, make it bulletproof

Week 4 — publishable

Pinned deps, GitHub Actions CI, smoke tests on every commit
One killer demo — show pause/inject/resume doing something you literally cannot do with TinyFish. That's the README hero
README structure: problem → demo gif → one import → feature table → quickstart
PyPI publish