# Label adjudication

WideSWE marks a repository as a target (`included_in_task`) when one of the change's pull
requests touched it. That is a seed label for complete-set discovery, not the full
required set:
- a repository can change without being needed for the request (a version bump that
  rides along);
- a needed repository can be absent (the change landed elsewhere, or later).

This protocol turns the seed labels into reviewed ones. `adjudicate.py` writes the sheets,
measures agreement and applies the verdicts. None of its outputs are committed, since they
carry case IDs and WideSWE labels.

## Protocol

1. **Sheets.** `adjudicate.py sheets --cases CASES --split dev --out DIR` writes one
   Markdown sheet per case and `DIR/review.csv`. Do dev first, then test before any test
   case is scored. Reviewers see the request, each target's pull requests and the
   context repositories, never a ranker's output.
2. **Two independent reviewers.** Each copies `review.csv` (to `review-a.csv` and
   `review-b.csv`) and marks every repository:
   - `required`: the request can't be met without changing this repository;
   - `affected`: it changed in the historical change, but the request doesn't need it;
   - `not-needed`: neither.

   On the case's `(missing)` row, list any required repository the case lacks, as
   `owner/name`, with the evidence in `notes`: a linked pull request, an import, or an
   API the request uses. Reviewers read the pull requests and the code at the base
   commit. They don't discuss cases until both have finished.
3. **Agreement.** `adjudicate.py agreement review-a.csv review-b.csv` prints raw
   agreement, Cohen's kappa on the three labels and on required-versus-not, and every
   disagreement. Report both kappas.
4. **Adjudication.** The reviewers resolve each disagreement together, or a third person
   decides, and write `final.csv`. Record the reason for every change from WideSWE's
   label.
5. **Apply.** `adjudicate.py apply --cases CASES --verdicts final.csv --out CASES-adj`
   writes a cases file whose gold is the required set. A missing repository joins the gold
   only when it is one of the case's context repositories, which the universes hold at a
   pinned commit; any other is listed, and the paper reports it as a label the benchmark
   can't score. A case with no required repository is dropped and listed.
6. **Scoring.** The preregistered primary analysis scores the adjudicated labels and
   reports WideSWE's labels as a sensitivity analysis, or the reverse when adjudication
   can't finish before the test run. The preregistration fixes which.

Reviewing a case takes reading one to three pull requests. Budget about 10 minutes a case
per reviewer, so about 20 hours of reviewer time for all 120 cases.
