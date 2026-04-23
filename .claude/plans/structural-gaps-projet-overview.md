# Structural gaps vs `projet-overview.md`

Reference: [`.ballast_memory/projet-overview.md`](../../.ballast_memory/projet-overview.md) — what is still missing for the full vision (especially the “what success looks like” workflow, overview lines ~866–897).

This note is **priority-ordered**: Tier 1 blocks end-to-end distributed success; Tier 2 is the publishable thesis; Tier 3 is optional or parked.

---

## Tier 1 — Without these, the success workflow (overview ~866–897) cannot run end-to-end

These are the pieces that make Ballast a **distributed system** rather than a **local library**. Right now you have a solid single-process kernel; the **M5↔M2 bridge** does not exist.

### 1. M5 spec server — `GET /spec/{job_id}/current` (overview ~71, ~333–344)

`ballast/core/server.py` already exposes `GET /spec/{job_id}/current` and `POST /spec/{job_id}/update` (in-memory per process). That is enough for local / dev polling with `SpecPoller`.

**Still missing for production M5:** durable storage, **version chain** (`parent_hash` history), auth beyond the optional `BALLAST_SPEC_SERVER_TOKEN` on POST, and a first-class `POST /spec/{job_id}` that locks and persists successive versions.

### 2. M5 job dispatcher → M2 (overview ~67–73, ~866–871)

“M5 dispatches jobs to M2 via SSH/HTTP” — **no dispatcher module** exists.

**Need:** Something that registers `job_id`, posts the initial `SpecModel` to the spec server, and ships the task to a worker.

### 3. `adapters/smolagents.py` — build sequence **step 12**, missing

The **M2 worker** story depends on this. Today `trajectory.py` runs against pydantic-ai’s `Agent.iter`; the smolagents adapter is what makes the **worker side** of the M5/M2 split real.

---

## Tier 2 — The publishable artifact (the “2027 thesis”)

Overview ~11 and ~778–799: the **training dataset** is the credential. It is **not** being written today.

### 4. `DriftLabel` dataset writer (overview ~781–795)

`NodeSummary` in `checkpoint.py` captures **run-level** progress (different fields). The overview’s `DriftLabel` schema captures **training-example-level** data (e.g. `input_tokens` / `output_tokens`, full `node_content`, `context_snapshot`, `model_used`, `confidence`).

Today every run produces `ballast-progress.json` for resume. **No** run produces a JSONL of `DriftLabel` rows for training — audit harness without the **data extraction layer**.

**Need:** e.g. `ballast/core/dataset.py` (or `data/`) that emits **one `DriftLabel` row per scored node**, partitioned by `spec_hash`, written to disk as **JSONL**.

### 5. Layer 3 local scorer — build sequence **step 15**, missing

Layer 2 labels exist to distill **Layer 3**. Today there is **no** distillation script, **no** local scorer, **no** “Layer 3 first, fall back to Layer 2 only on novel nodes” path.

Blocked in practice until **#4** exists.

---

## Tier 3 — Optional or parked (still in the overview)

| Item | Notes |
|------|--------|
| `adapters/langgraph.py` | Step 14; overview marks it **optional** but listed. |
| `probe.py` **registry pattern** (overview ~672–702) | Overview: `register_probe` + `_PROBE_REGISTRY` + e.g. `probe_write_file`. **Re-check** current `probe.py` vs that contract — if shape differs, “operators register probes per tool” may not be expressible. |
| ZenML pipeline wrapper | Step 16; **phase 5** — park. |
| AG-UI browser dashboard | **Phase 5** — park. |

---

## Recommended next build: M5 spec server + version chain

Given the kernel is in place, the **next plan** should be the **M5 spec server** (extend `core/server.py` or add a dedicated module). Concretely:

```http
GET  /spec/{job_id}/current   → latest SpecModel JSON
POST /spec/{job_id}           → new spec body; lock with parent_hash chain
GET  /spec/{job_id}/history   → full version chain (audit)
```

### Why this before smolagents

1. **Without the server**, `SpecPoller` effectively always gets `None` — Invariant 2 cannot be exercised except under full mocks; you cannot **prove** live spec propagation.
2. **Once it exists**, you can add an integration test: server in one thread, `run_with_spec` in another, and assert **delta injection** at a node boundary.
3. **After that**, smolagents adapter and the dataset writer are largely **independent** and can proceed in parallel.

Optional follow-up: `/create-plan` for **M5 spec server + version chain storage** as the next implementation step.
