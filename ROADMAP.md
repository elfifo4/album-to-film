# Future improvements

Ideas that were discussed and deliberately left for later. Nothing here is built.

## Settings screen

A settings page in the review site for per-user preferences, so optional behaviour can be switched on
without editing config files. Preferences would be stored locally (not in the repository), and every
option would default to today's behaviour.

First candidate setting:

### Auto quick preview (optional, off by default)

Render a quick preview (540p draft) automatically whenever something a film depends on changes, so
the Render tab already shows the updated film without pressing a button.

Intended behaviour:

- **Wait before rendering.** Start only a few seconds after the last change, so a run of edits
  (several corners, several stars) triggers one render, not one per click.
- **Quick preview only.** The 1080p version stays manual and is still marked as changed until it is
  rendered.
- **Only the films affected.** A change to a photograph used in one film does not re-render the other.
- **A change during a render** cancels it and starts again after the wait.
- **Only while the page is open.** Changes made before the browser was closed are picked up the next
  time the page is opened.

Why it is optional: the machine works in the background after every run of edits, which is noticeable
on a laptop (fan, battery), especially for the full movie. People who make many edits and then watch
once are better served by the manual buttons.

Where it would hook in: the Render tab already knows when a film has changed since its last render
(`film_state` and `render_status` in `album_to_film/review.py`), and renders already run as
cancellable background jobs (`RenderJobs`). The feature is a timer on the page that calls the
existing render-start action for films marked as changed.
