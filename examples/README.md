# Synthetic demonstration packets

These PDFs and their JSON source text were authored from scratch for the public demonstration.
They contain no real parties, source agreements, private document excerpts, or production extraction results.
They are fictional test material, not legal advice or agreement templates for adoption.

- `pdfs/equipment-loan.pdf` describes portable observation kits and practice sessions.
- `pdfs/activity-workshop.pdf` describes reusable activity stations and making workshops.
- `sources/` contains the editable text, tables, and explicit page boundaries used to generate each PDF.
- `expected.json` records authored expectations, independently of model outputs.

Both packets use the same deliberate structure so the workflow can be compared across two inputs.
Each has four pages, an overview, two source documents with independent clause numbering, mitigation proposals, and excluded notes.
Document A clause 4 crosses the first page boundary and includes nested exceptions; its extracted parent must include those exceptions and clause 4.4.
Clause 7 includes a table that belongs to the parent clause.
Document B repeats clause numbers 4, 7, and 12 but has different obligations and titles.

The mitigation section contains six bodies and seven headers.
One body covers two Document A headers, six headers should find source clauses, and the outdoor-weather header intentionally has no match.
The clause 99 reference in the notes is intentionally outside the extraction scope.
These are desired fixture outcomes, not a claim that every model or matching threshold will achieve them.

Regenerate the PDFs from the repository root with `uv run --group examples python scripts/generate_examples.py`.
The generator requires ReportLab and uses deterministic PDF metadata, fixed page boundaries, built-in fonts, and an explicit synthetic author.
No credentials, model calls, or application data are needed to regenerate them.
