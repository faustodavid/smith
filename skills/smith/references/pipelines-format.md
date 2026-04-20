# Pipelines List Output Format

`smith <remote> pipelines list <scope> <id>` renders a compact DAG using a line-level grammar designed for minimal token usage while remaining unambiguous. Every line starts with a single-character prefix (`@`, `#`, `*`, `<`, `>>`) that names the entity type.

## Grammar (one per line)

| Prefix | Entity | Form |
|---|---|---|
| `@` | Pipeline header | `@p:<ID>\|prj:<ID_OR_PATH>\|ref:<NAME>\|st:<STATUS>` |
| `#` | Stage header | `#<STAGE_NAME>` |
| `*` | Job | `*j<ID>:<NAME>[MATRIX]\|<STATUS>\|<DURATION><ATTRS><ENV> <NEEDS >> DOWNSTREAM` |
| `<` | Needs (inline) | `<dep1,dep2,...` — IDs prefixed with `j` when uniquely resolvable, otherwise raw names |
| `>>` | Downstream trigger (inline) | `>> <PROJECT>:<PIPELINE_ID>[<STATUS>]` |

- **Status codes** are 2–4 letters (`ok`, `err`, `run`, `skip`, `man`, `can`, `pen`, `cre`, `sch`, `wait`, `prep`). Unknown statuses fall back to the first four letters of the raw value. Missing status renders as `-`.
- **Duration** is always seconds with a `s` suffix (e.g. `42s`). Missing duration renders as `-`.
- **Attributes** (zero or more, order fixed): `!` for allow-failure on `err`/`can`, `?` for manual jobs.
- **Environment** appears as `^<ENV>` immediately after attributes (e.g. `^prod`).
- **Needs** are comma-separated on the same line as the job, prefixed with ` <`. Each entry is `j<ID>` when the name resolves to exactly one job in the current pipeline; otherwise the raw name is emitted.
- **Matrix indices** are rendered as `[<IDX>/<TOTAL>]` appended to the job name. The renderer auto-parses `"<name> <i>/<n>"`, `"<name> [<i>/<n>]"`, and `"<name> (<i>/<n>)"` into a base name plus matrix tuple when no explicit `matrix` is provided.
- **Downstream trigger** is appended after the needs segment, separated by ` >> `.

## Example

```
@p:998877|prj:882|ref:feat/api|st:run
#build
*j10:compile|ok|200s
#test
*j21:unit[1/2]|ok|60s <j10
*j22:unit[2/2]|ok|65s <j10
*j23:lint|err|40s! <j10
#deploy
*j30:staging_up|ok|300s^staging <unit
*j31:prod_up|man|0s?^prod <j30 >> ops/infra:1122[cre]
@p:1122|prj:900|ref:main|st:cre
returned_count: 2
total_count: 2
```

Reading tips:

- Each `@` block is a full pipeline. Downstream pipelines appear as their own `@` blocks later in the stream; the `>> <proj>:<id>[<status>]` marker on the trigger job links to the downstream block.
- Jobs without an enclosing `#` header belong to a stage-less pipeline (GitHub Actions, Azure Classic without stages).
- When a `needs` entry references a name that resolves to multiple jobs (e.g. a fan-in over a matrix), the raw name is kept to avoid ambiguity.
- `returned_count` and `total_count` close the payload. A warning line appears above them when the output is `partial: true`.

## Per-provider coverage

- **GitLab**: GraphQL delivers stages, jobs, matrix, `needs`, manual, `allow_failure`, environment, and downstream pipeline triggers. REST is the automatic fallback (emits the `@` headers only; adds a `GraphQL unavailable` warning and sets `partial: true`).
- **GitHub Actions**: REST `/actions/runs/<id>/jobs` populates a flat jobs list (no stage headers). Matrix entries are extracted from the job name when possible.
- **Azure DevOps**: Build `/timeline` populates stages and jobs. `succeededWithIssues` renders as `ok` with the `!` attribute.
