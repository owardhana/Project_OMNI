# ADR 0001 — Store tissue weights as flat float properties, not a map

Status: Accepted (2026-06-15)

## Context

The MVP spec (02_mvp.md, 04_decisions.md) models the PRODUCES edge with a single
map-valued property:

```cypher
(:Gene)-[:PRODUCES { tissue_weights: { whole_blood: 0.73, liver: 0.45, brain_prefrontal_cortex: 0.88 } }]->(:Transcript)
```

and filters with `tissue_weights[tissue] > 0.3`.

Neo4j 5 does **not** allow map values as node/relationship properties. Verified
empirically on the live container:

```
CREATE (a)-[r:PROBE {tissue_weights:{liver:1.0}}]->(b)
-> Property values can only be of primitive types or arrays thereof.
```

A flat float property works:

```
CREATE (a)-[r:PROBE {tw_liver:1.0, tw_whole_blood:0.5}]->(b)   -- OK
```

## Decision

Store tissue weights in Neo4j as **flat per-tissue float properties** on the
PRODUCES relationship, one per tissue in `settings.tissues`:

- `tw_whole_blood`
- `tw_liver`
- `tw_brain_prefrontal_cortex`

Reconstruct the `tissue_weights: {...}` dict in the **API/Pydantic layer** so the
external contract (GraphEdge, EdgeDetail) and the frontend stay exactly as
specced. The map shape is a presentation concern; the DB stores primitives.

Naming convention: `tw_<tissue_key>` where `<tissue_key>` is the value from
`TISSUES` env var (e.g. `tw_brain_prefrontal_cortex`).

## Consequences

Affected by this decision:

- **ETL** (`etl/03_gtex.py`): `SET r.tw_<tissue> = <value>` per tissue.
- **Queries** (`backend/db/queries/genes.py`): tissue filter is
  `r.tw_<tissue> > $threshold` (build the property name from the tissue param;
  validate the tissue against `settings.tissues` to avoid Cypher injection).
- **Text2Cypher prompt examples**: use `r.tw_liver > 0.3`, not map indexing.
- **API layer**: reassemble `tissue_weights` dict from the `tw_*` fields before
  returning `GraphEdge`/`EdgeDetail`.
- **Frontend**: unchanged — still receives `tissue_weights` as an object.
