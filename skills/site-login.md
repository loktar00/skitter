# Site Login Skill

You have access to a remote browser that can open sites for manual login. When the user needs to authenticate with a website, guide them through the login process and save the session.

## When to use this

The user says something like:
- "Log me into YouTube"
- "I need to sign in to Facebook"
- "Set up authentication for eBay"
- A browser action fails because the site requires login

## Process

### Step 1: Check existing sessions

Call `crawler_login_sessions` to see if there are already saved cookies for the target site.

If the domain is already listed, tell the user they're already logged in. If the session has expired (actions are redirecting to login pages), continue to step 2.

### Step 2: Open the login page

Call `crawler_login_open` with:
- `url` — the site's login page (e.g. `https://accounts.google.com`, `https://www.facebook.com/login`, `https://signin.ebay.com`)
- `label` — friendly name (e.g. "YouTube", "Facebook", "eBay")

### Step 3: Guide the user

Tell the user:
1. Open the **dashboard** in their browser (the crawler dashboard URL)
2. Go to the **Browser View** tab to see the live browser
3. Complete the login (enter username, password, 2FA if needed)
4. Come back and tell you when they're done

### Step 4: Save the session

Once the user confirms they've logged in:
1. Call `crawler_login_save` to capture the cookies
2. This saves cookies to disk — all future browser sessions and crawls will use them automatically

### Step 5: Confirm

Tell the user:
- Login session saved for `<domain>`
- All future browser actions and crawls on this site will be authenticated
- If the session expires later, they can repeat this process

## Common login URLs

| Site | Login URL |
|------|-----------|
| YouTube/Google | `https://accounts.google.com` |
| Facebook | `https://www.facebook.com/login` |
| eBay | `https://signin.ebay.com` |
| Amazon | `https://www.amazon.com/ap/signin` |
| Twitter/X | `https://x.com/i/flow/login` |
| Instagram | `https://www.instagram.com/accounts/login/` |
| LinkedIn | `https://www.linkedin.com/login` |
| Reddit | `https://www.reddit.com/login/` |

## If login fails

- The session may have expired — repeat the process
- Some sites have aggressive bot detection — tell the user to try logging in slowly and naturally via the Browser View
- 2FA codes need to be entered in the live browser — the agent cannot do this
- If cookies aren't persisting, check that the crawler dashboard shows the domain in Login > Saved Sessions
