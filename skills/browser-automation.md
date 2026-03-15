# Browser Automation Skill

You have access to a remote browser via MCP tools. You can navigate websites, click elements, type text, and read page content — all on a server that has saved login sessions (YouTube, Facebook, etc.).

## How to handle browser tasks

When the user asks you to do something in a browser (e.g. "get my YouTube notifications", "check my eBay purchases"), follow this process:

### Phase 1: Explore (figure it out)

1. Call `browser_open` to start the browser (loads saved login cookies automatically)
2. Use `browser_navigate` to go to the target site
3. Use `browser_snapshot` to read the page and understand the layout
4. Use `browser_click`, `browser_scroll`, `browser_type` etc. to interact
5. Use `browser_snapshot` after each action to see what changed
6. Keep going until you successfully complete the task
7. Call `browser_close` when done

Take note of the **exact sequence of actions** that worked. Ignore dead ends, wrong clicks, and exploratory snapshots — identify only the essential steps.

### Phase 2: Record (capture the clean flow)

Once you've figured out the steps:

1. Call `browser_open` to start a fresh session
2. Call `browser_record_start` to begin recording
3. Execute **only** the essential steps you identified — no exploratory snapshots, no wrong turns, just the clean path:
   - `browser_navigate` to the right URL
   - `browser_click` the right elements
   - `browser_type` if needed
   - `browser_scroll` if needed
4. Once you reach the goal, call `browser_record_stop` to see the captured steps
5. Call `browser_record_save` with a clear, descriptive name (e.g. `youtube_notifications`, `ebay_recent_purchases`)
6. Call `browser_close`

### Phase 3: Report

Tell the user:
- What you found/accomplished
- That you've saved a workflow called `<name>`
- That next time they can just say "run the `<name>` workflow" and it will execute instantly without AI

## If a saved workflow already exists

If the user asks for something and you know a workflow already exists for it (check with `crawler_list_workflows`), skip exploration and recording — just run it:

1. Call `crawler_run_workflow` with the workflow name
2. Report the results

## Tips

- `browser_snapshot` is your eyes — call it after every action to see what happened
- Click by visible text (`text: "Notifications"`) rather than CSS selectors when possible — it's more resilient
- If a page uses infinite scroll, use `browser_scroll` then `browser_snapshot` to load more content
- If the site asks you to log in, tell the user to use the Login tab on the dashboard first, then try again
- Keep workflows focused — one workflow per task, not one giant workflow for everything

## Naming conventions

Use lowercase with underscores for workflow names:
- `youtube_notifications`
- `youtube_watch_latest`
- `ebay_recent_purchases`
- `facebook_group_posts`
