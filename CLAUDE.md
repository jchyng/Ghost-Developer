## Rules

### Screenshots

- All Playwright-generated screenshots must be saved to the `screenshots/` folder.
- The `screenshots/` folder is git-ignored. Do not commit its contents.
- When calling `mcp__playwright__browser_take_screenshot`, always set `filename` to `screenshots/<name>.png`.

### Commits

- Each commit must represent a single logical unit of work (one feature, one fix, one refactor).
- Do not bundle unrelated changes into one commit.
- Write clear, descriptive commit messages that explain _why_, not just _what_.
- Prefer many small commits over one large commit — this makes `git bisect`, code review, and rollback easier.
