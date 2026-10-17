# Connected official information candidate

This candidate collects bounded official public sources into an explicit disposable canonical SQLite workspace, projects source-bound information units, and supplies those units to the common ask/SSE retrieval path. It is staged and disabled by default. It does not establish complete university coverage, human answer quality, freshness of the historical CSV snapshot, or production approval.

## Data and evidence contracts

`SourceDocument.document_key` remains canonical. Existing source keys are preserved during public snapshot import and notice attachment backfill. New static HTML uses dataset `guidance` / source_type `official_public_page`; supported files use `attachments` / `official_attachment`; recognized CMS notices retain dataset `notices`. Main-site notices retain `board:id`, other CMS sites use `host:board:id`. Navigation `pageIndex` is stripped only for known CMS detail identities. Meaningful download and other query parameters remain part of identity.

The collection checkpoint stores the frontier, durable deferred work, discoveries, exact institution entries, current/historical policy versions, response receipts and receipt history. Raw SHA-256 bodies live under the staging directory. Receipt import rechecks allowlisting and hashes and reproduces extraction from the raw body before accepting it. Failed, unsupported, partial or missing fetches never withdraw or replace a valid published canonical body. Content changes retain identity with a new hash/version. Hidden/withdrawn canonical records are not silently reactivated.

`connected_*` tables and `information_*` tables are derived. The original structured ontology is preserved. All active/updated datasets receive document nodes and searchable source chunks, including generic notices/rules, meals and unknown future datasets. Document/website/office links are peripheral inspection facets. Website/department membership cannot establish eligibility, authority, course satisfaction or supersession.

The information projection has these contracts:

- A subject is a process, specific program, requirement, course, event, facility or dated meal offering. Explicit campus, college/program/department, entry cohort, academic event year/semester, curriculum year, date and visibility distinguish identity. Unknown metadata remains unknown. New page import uses `audience=unknown` until explicit content identity/heading/label evidence establishes degree scope. Mixed university menu breadcrumbs and incidental graduate/office words cannot declare graduate-only applicability; section-specific conditions stay scoped. Private subjects are source-isolated.
- A unit is an exact canonical field or section span, keyed by document, subject, locator, offsets and text. A compound document can support multiple subjects without key collisions. Heading hierarchy carries the enclosing topic into nested conditions; sibling topics end inheritance.
- Assertions classify exact source paragraphs/table rows as eligibility, requirements, exceptions, application/decision/result timing, documents, procedures, amounts, rules, course references, credits, consequences, approval conditions, location or operating state. They preserve full condition text and modal language. Dependent applicability contexts have validated source spans, review states and explicit branch identities/relations. A known local semester filters sibling applicant branches; source alternatives stay alternatives. Review-required tables cannot re-enter selector/generation through nested contexts or overlapping lexical chunks. They do not evaluate an individual's eligibility or GPA.
- Every assertion carries its canonical key, source URL or verified local snapshot status, source locator/text, derivation, review status, document/corpus revision and applicability qualifiers. Publication validates reference integrity, assertion/unit subject agreement and exact source spans before atomic replacement.
- A topic navigation link is distinct from a substantive assertion. Same-topic scope links do not inherit applicability. Hyperlinks and attachment references do not make an attachment a required submission form. Course credit is an attribute; a course requirement requires a validated explicit clause/code association.
- Ambiguous flattened HWP/PDF table columns remain `needs_table_layout_review` and are exposed separately from known evidence. Unknown subjects and unclassified units remain searchable/inspectable and appear in coverage reports.

For the source-based 전공결정 case, the general guidance, the program-specific notice, its HWP plan and existing 2025/2026 guides contribute different facets. Foreign freshmen admitted in 2026-2 have a source-stated 2027-1 deferral without invented exact dates. HWP's 2026-10-19 09:00–10-23 23:59 window is separate from notice publication on October 16. HWP's November 6 result date and notice's “추후 안내 예정” remain separate source statements; neither automatically supersedes the other. Business-specific and default/special application channels retain their explicit source applicability. An entry cohort is not an academic event year.

Existing calendar adapters distinguish managing office from student major/audience and legacy `published_at=start_date` from actual upstream publication. Explicit event title year overrides a calendar container caption. An already published future academic event is usable; unpublished future meal blanks are not. Meal retrieval keeps the request reference/as_of separate from the serving date. It requires a nonempty observed/published menu or explicit closure. Future target dates require evidence already known by the reference date; a legacy serving date is not treated as publication. The actual canonical `SourceDocument.collected_at` records observation evidence when upstream publication is unknown, with a separate provenance role; this timestamp and raw payload participate in revision invalidation. CLI `--meal-date` sets the serving date while `--as-of` remains the knowledge reference.

## Inventory and supported collection

The directory adapter reads only rendered `#campus_list .item` / `h2.cont_tit` / `ul.link_group a` entries. The checked fixture contains 188 institution entries and 179 hosts. Global menu/footer links are a separate discovery frontier. The source manifest includes 158 distinct HTTPS institution roots supported by 162 same-host HTTPS verification/upgrade records. These are approved collection origins, not proof of institution content coverage or campus applicability. Original HTTP-listed URLs and provenance are retained. Official-linked external domains and unverified destinations remain review candidates. WISE is excluded; Seoul/BMC/shared are the rollout priority and unknown campus remains explicit.

The bounded collector follows only exact approved HTTPS origins/path prefixes, validates every redirect and DNS destination, pins a public address for the TLS connection, and preserves hostname certificate verification. It does not traverse login/mutation routes, execute page scripts, enumerate guessed numeric URLs or trust a domain suffix. Budgets cover requests, pages, bytes per response/total, depth, elapsed time, redirects, active frontier and per-host pacing. Review/history rows do not consume the active frontier cap. Deferred rows preserve parent identity and are admitted as prior work finishes; historical overflow discoveries can also be resumed. Pagination on validated CMS listing anchors does not consume normal navigation depth.

Official hosting is not source authority: `/qna/list` and recognized unreviewed user-board routes retain raw receipts in an explicit authority-review state and cannot import active official assertions. Existing staged copies of those routes cannot seed connected retrieval or semantic projection. Static official FAQ guidance remains supported; verified official answer portions need a future explicit adapter.

HTML adapters preserve source sections, tables and body links; discovery links never become body evidence. Main and shared departmental CMS use `.board_view .view_cont`, `#content_focus.contents > .inner` and `#sub_container #contents`. Known `downGO` download calls are parsed as literal arguments without executing JavaScript. File query path encoding is supported while traversal and ambiguous sensitive query keys are rejected. Page-specific contacts are optional leaves, distinct from source owner or student department.

PDF text, HWP OLE records, HWPX, DOCX, PPTX and XLSX are supported without models. A subprocess bounds attachment time/CPU/memory and archive/XML/cell/page/character sizes. Legacy DOC/XLS/PPT, encrypted/malformed files and unknown formats report explicit unsupported/failed states. Scans and image-only notices preserve resources and report OCR-required. OCR is not supplied by this collector. Homepage shells with useful links are discovery-only, not substantive information. Unsupported layouts/API sites remain incomplete until an adapter is reviewed.

## Safe staging commands

Run from `src/RAG` with this worktree's freshly installed venv. All mutation commands require an explicit disposable directory and marker. They reject serving/default paths and symlinks. No command below publishes to Chroma or serving indexes, runs a production migration or grants promotion approval.

```sh
UV_CACHE_DIR="$PWD/.uv-cache" uv venv --python 3.11 .venv
UV_CACHE_DIR="$PWD/.uv-cache" uv pip install --python .venv/bin/python -r requirements-dev.txt

STAGE="$PWD/artifacts/ontology_evaluations/official-review"
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" init

# Only an explicitly supplied, approved public canonical snapshot; source is
# opened read-only. SHA must match. Import does not overwrite target conflicts.
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" import-snapshot \
  --snapshot /absolute/public/snapshot.db --snapshot-sha256 APPROVED_SHA256 \
  --acknowledge-public-snapshot

# Live public execution belongs to root's reviewed collection run.
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" collect \
  --manifest data/official_public_inventory.json --pages 100 --requests 150 \
  --total-bytes 30000000 --response-bytes 8000000 --depth 3 --seconds 300 \
  --per-host-seconds 1 --frontier 5000 --backfill-attachments

.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" import
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" build
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" bundle \
  --query '2026학년도 2학기 전공결정 대상 기간 방법 예외' --as-of 2026-10-17
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" search \
  --query '2025년과 2026년 2학기 전공결정 신청기간 비교' --as-of 2026-10-17
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" inspect \
  --seed 'notices:EXACT_CANONICAL_ID' --as-of 2026-10-17
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" coverage
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" export --output "$STAGE/graph.json"
```

A bounded partial collect/import exits 1 and retains work; operational exceptions exit 2. Inspect state/reason rather than interpreting a nonzero exit as data loss. Resume uses the same collect command/checkpoint. `--retry-failed` revisits failed requests; `--refresh` recrawls extracted/discovery-only records. Reviewed manifest upgrades preserve policies, receipts, frontier and history; they do not require discarding a checkpoint. A parser change requires refresh/re-extraction before import. Review candidates require a concrete verified allowlist entry; they do not become trusted automatically.

`bundle` reports unique units, all actual facets, requested/available/missing/not-selected facets, per-year coverage, source differences, missing calendar windows, conditional term notes, review candidates, references and explicit truncation. `inspect` is bounded read-only document path navigation. Public target department/college is resolved only from currently authorized public canonical metadata, after parent-chain checks. It cannot grant private access; private duplicates cannot change public reference resolution. CLI `--department` is trusted requester scope; query target wording cannot grant another department's access. `--college`, `--program`, `--entry-year`, `--academic-year`/`--academic-years`, `--semester`, `--campus`, `--audience` and mandatory `--as-of` constrain inspection/search. A CLI operator must not impersonate a requester for public deployment.

A fully offline synthetic smoke run is also available:

```sh
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" seed-fixture \
  --fixture tests/fixtures/connected_official/corpus.json
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" build
.venv/bin/python scripts/connected_official_knowledge.py --staging-dir "$STAGE" replay \
  --fixtures tests/fixtures/connected_official/cases.json --as-of 2026-10-17
```

Use a separate empty stage for `seed-fixture`; it refuses a populated corpus. Replay compares the same corpus's lexical source index with connected retrieval. It is not a comparison against the production dense/hybrid index or human answers.

## Runtime and maintenance

`RAG_CONNECTED_RETRIEVAL_ENABLED=0` and `RAG_CONNECTED_MAINTENANCE_ENABLED=0` are default. `api/rag_service.py::_execute_retrieval_steps` calls `_connected_retrieval_frames` once with the already resolved query. Both `/ask` and `/ask/stream` use this path. `connected_search(DATABASE_FILE, ...)` reads the actual canonical database, not a separately self-consistent copy. A missing, stale, corrupt or incompatible graph returns a logged fallback and no supplement.

The reader validates canonical generation, graph checksum, schema, reviewed aliases, concept/provenance dependencies and revision. Relevant SQLite triggers invalidate canonical/graph generations; an unrelated query-log insert does not reload all documents. The cache holds at most two validated generations. FTS5 trigram bounds lexical search; information queries resolve subjects and bounded source assertions. Visibility, campus, audience, current status/revision, academic/cohort scope and effective time are checked before limits; attachments recursively require current parent permissions/status. Private documents cannot seed public identity resolution or fill public traversal limits. Same-campus/topic links do not override filters.

Information candidates are at most six documents per original request, with rank contributions separated from lexical/dense score scales. Generic lexical supplements are at most three. Baseline strong evidence remains in the normal balanced shortlist. Graph candidates are not `structured_match`; the normal evidence selector and grounding remain mandatory. Selector refusal does not resurrect rejected graph candidates. Source-bound information units carry applicability into selector input; complete units are packed into the existing generation context budget. Selector input also packs whole units. If no complete evidence fits, `information_budget_exceeded` terminates with no answer and no profile-only generation; partial affirmative slicing is forbidden. Fitting units from other sources remain usable. Raw lexical chunks split at verified review boundaries so valid neighboring paragraphs remain searchable with exact source offsets. Lexical candidates carry complete source units into the same packing guard. Omitted units are logged as `information_context_truncated`; absence from a bounded generation context is not proof that the source lacks that facet. Raw graph predicates do not enter answer text as official facts. Original citation/source metadata stays attached to materialized snippets.

`run_post_ingestion_dag` optionally rebuilds the connected information projection only after existing canonical/index lineage gates, under the existing serialized ingest writer lock. Failure is a derivative failure/partial lifecycle state. This does not bypass publication approval or index readiness. Turning flags on, promoting collected canonical data to serving indexes, migrating a production schema, or deploying requires the existing root/owner process; none was performed here.

Evaluation fingerprints and semantic cache namespace include the new retrieval flag and connected schema/revision/alias/projection provenance. A baseline and connected candidate cannot share the same fingerprint.

## Validation and remaining rollout

`tests/test_information_graph.py` uses exact public fixture excerpts and the actual 54,784-byte HWP (SHA-256 `44397835494a08aa58e431a3496b8079faf63efd0c3417b09e3955926da27ca5`). It checks hierarchy, college identity, unit membership, conditions/exceptions, temporal scope, plan versus form, unreviewed tables, calendar overloaded fields, active frontier batches, unrelated log-cache writes, normal-ingestion guards and actual ASGI ask/SSE selector/grounding transports. Model responses are deterministic fixtures; no paid calls occur. `tests/test_connected_official_knowledge.py` covers collection/import/graph lifecycle and adversarial scope/provenance boundaries.

The root-supplied offline `run_information_case.py --staging-dir <NEW absolute worktree directory>` replays 9,498 tracked public snapshot rows plus G/N/H and generates `information-case-evaluation.json`. Never reuse or modify root's older case artifacts. Then run:

```sh
.venv/bin/python scripts/evaluate_information_case.py --staging-dir "$CASE_STAGE" --as-of 2026-10-17
```

Its source oracle is agent-authored and measures expected source values, scope and unknowns. The seven query bundles include all five root competencies plus broader A1–A12 and missing-prerequisite checks. `evaluate_connected_staging.py` can replay a supplied public snapshot and cached HTTP checkpoint, accept `--source-fixtures`, and report expected URL ranks, subject/facet/source coverage and cold/warm timings. `--scale-copies 2` is an explicitly synthetic growth workload, not additional facts.

| Outcome | State and limits |
|---|---|
| Official inventory/frontier | Implemented; exact 188-entry fixture verified, 158 reviewed HTTPS roots enabled. Source bodies beyond captured fixtures require continued bounded collection. |
| HTML/CMS/attachments | Implemented and fixture tested; 22 cached official guidance pages and real G/N/H replayed. Unsupported API/layout/OCR/legacy formats remain explicit incomplete work. |
| Canonical/index/graph lifecycle | Disposable canonical import, source FTS and atomic information projection implemented. No serving index rebuild/promotion or production migration executed. |
| Existing plus new semantic evidence | Actual tracked public snapshot replay includes existing guides and new source-bound conditions/periods/methods/exceptions. Current scale and oracle evidence is under ignored staging artifacts. Snapshot IDs are synthetic row identities, not proven production source IDs. |
| Ask/SSE | Common runtime integrated; deterministic ASGI fixtures exercise normal selector/grounding and resolved follow-ups. No live model/human answer-quality claim. |
| Broad public rollout | Root owns separate live collection; institution homepage discovery alone is incomplete. External-domain review, unsupported adapters/OCR, metadata/campus review, periodic recrawl and human semantic review remain. |
| Production/main | Not deployed, migrated, merged, committed or pushed. Full university knowledge coverage and churn improvement are unproven. |

Final command counts/timings belong to the implementation Completion Report and ignored evaluation manifests. Keep source-coverage, retrieval-only, deterministic transport, model evaluation and human answer quality separate. The complete user objective remains open until collection coverage, independent semantic/answer review and approved rollout are finished.

## Recorded candidate verification (2026-10-17)

The final normal, empty-DB and seed `8411223` randomized suites each passed 1,882 tests with one skip and 10 dependency deprecation warnings. The skip requires untracked real notice/lexical/Chroma artifacts; they were deliberately not read/copied. The flag-enabled golden/qrels, evidence-selection, grounding, campus and common-query regression subset passed 199 tests. The dedicated collector/information tests passed 103 tests. No existing test was removed, weakened or newly skipped.

The final mandatory offline 9,501-document G/N/H plus public-snapshot case passed the recorded agent-authored checks, including the five competency bundles and broader A1–A12/unknown-evidence checks. For the 9,520-document snapshot plus 22 cached guidance pages, all 12 expected official guidance URLs appeared within the top eight connected results (11 first, 휴학 second), compared with five in the same corpus's lexical results. This is source retrieval only. The 19,040-document doubled synthetic workload is not extra collected facts. Recorded build/latency values are indicative local observations; concurrent regression workloads also ran.

Exact commands, paths, exit codes, coverage, source ranks and reports are under `artifacts/ontology_evaluations/connected/`: `stable-test-commands.json`, `mandatory-case-command.json`, `semantic-public-command.json`, `semantic-scale2-command.json`, `final-cli-commands.json`, and their log/report files. These generated data and databases are ignored and must not be committed. The Completion Report identifies the final case directory and limitations. Independent root review and human answer evaluation remain separate gates.

Current-main compatibility is not verified in this feature branch. It remains on original base `8411b2232a02d8cc6c5754fabe7dcb5f8ca9af63`. Root reported newer main `a843d08065ee98960b817ddf980dab5f9d97221b`; its typed source-reference domains/legacy artifact loader require a separate guidance/attachments integration. Exact-document follow-ups must retain their terminal identity boundary and must not receive broad same-topic supplements. No newer-main code was imported or reimplemented here.
