# Lessons

What we learn about designing and running experiments: process, method and measurement pitfalls, infrastructure, and budgeting. A study's findings belong in its report, not here. The point is that each new experiment is designed better than the last.

Before designing an experiment, read [checklist.md](checklist.md). It distills these lessons, and each line links the lessons behind it.

## Layout

One short file per lesson, in a folder per topic. The file names are the lessons' titles, so a folder listing (`ls lessons/*/`) is the index. There is no list of lessons to keep up to date, so parallel PRs don't conflict.

| Folder | What goes there |
|---|---|
| [process/](process/) | Planning, scoping, reporting, the repo and the tracker |
| [method/](method/) | Designing methods, controls and the benchmark |
| [measurement/](measurement/) | Metrics, samples, caps, costs and what a model actually sees |
| [infrastructure/](infrastructure/) | VMs, storage, network, secrets and agent CLIs |
| [budget/](budget/) | Spend caps, cost logging and projections |

## Adding a lesson

Add lessons in the experiment's own PR, when it finishes.

- Create `lessons/<folder>/<short-rule-as-slug>.md` from the template below. Keep one point per file and keep it to about 20 lines.
- Positive lessons count too: use **What it saved.** in place of **What it cost.**
- If an existing lesson already makes your point, add a `**Seen again:**` line to it instead of a new file.
- If your lesson changes what to do before designing, add or edit one line in [checklist.md](checklist.md). On a rebase conflict there, keep both lines.

```markdown
# <What to do next time, as a short rule>

**Experiment:** <name> · **Added:** <YYYY-MM-DD> · **Source:** <PR, report or plan section>

**What happened.** <A few sentences, with the numbers.>

**What it cost.** <Time, money, reruns, or a result that can't be used.>

**Next time.** <What to do differently, concretely.>
```

## Sources

Link PRs by full URL. Plans and reports live in the Experiments Project's agent store, not in this repo. A source written as store `docs/v3-contract-joins.md` means `/cursor/stores/bc-816ea066-520c-4366-a62c-cdc514dd7aaf/docs/v3-contract-joins.md` on the Project's agents. Name the section too. Lessons the coordinator saw but nobody wrote down cite "coordinator's notes".
