# Recipe Builder Skill

You have access to a remote browser and a recipe-based web crawler. When the user wants to scrape a site, you can build a working recipe automatically by inspecting the page structure.

## When to use this

The user says something like:
- "Scrape the product listings from this site"
- "Get all the event links from this page"
- "Build a recipe for this URL"

## Process

### Step 1: Inspect the page

1. Call `browser_open` to start the browser
2. Call `browser_navigate` to the target URL
3. Call `browser_snapshot` to read the page content and get a sense of the structure
4. Call `browser_evaluate` with JavaScript to inspect the DOM:

```javascript
// Find repeated item containers
JSON.stringify(
  Array.from(document.querySelectorAll('*')).reduce((acc, el) => {
    const tag = el.tagName + (el.className ? '.' + el.className.split(' ')[0] : '');
    acc[tag] = (acc[tag] || 0) + 1;
    return acc;
  }, {}),
  null, 2
)
```

5. Look for repeating elements (items in a list typically share the same class/structure)
6. Use `browser_evaluate` to test candidate selectors:

```javascript
// Test a selector
document.querySelectorAll('div.product-card').length
```

### Step 2: Identify selectors

You need three things:
- **list_scope_css** — CSS selector for each item container (the repeating element)
- **item_link_css** — CSS selector for the link within each item (usually `a[href]`)
- **pagination** — How to get to the next page (next button, page links, or URL pattern)

Test selectors with `browser_evaluate`:

```javascript
// Count matches for the scope selector
document.querySelectorAll('div.item').length

// Check that links exist within each item
document.querySelectorAll('div.item a[href]').length

// Find the next page button
document.querySelector('a.next')?.href
```

### Step 3: Check pagination

Look for:
- A "Next" button/link → `type: next` with `next_css`
- Numbered page links → `type: all_links` with `pagination_scope_css`
- URL patterns like `?page=1`, `?page=2` → `type: url_template`

### Step 4: Create the recipe

Call `crawler_create_recipe` with the discovered selectors:

```json
{
  "name": "site_name_items",
  "start_urls": ["https://example.com/items"],
  "list_scope_css": "div.item-card",
  "item_link_css": "a.item-link",
  "pagination_type": "next",
  "next_css": "a.next-page",
  "max_list_pages": 10,
  "max_items": 200
}
```

### Step 5: Test with dry run

Call `crawler_run_recipe` with the recipe path to test it. Check `crawler_task_status` to see if items were found.

If it didn't work, go back to step 2 and refine the selectors.

### Step 6: Report

Tell the user:
- The recipe name and what selectors you chose
- How many items were found in the dry run
- How to run it: `crawler_run_recipe` with the recipe path

## Tips

- Start broad with selectors, then narrow down
- Items usually share a common parent class like `.item`, `.card`, `.listing`, `.post`
- Links within items are usually the first `a[href]` or one with a specific class
- If the site uses JavaScript rendering, the snapshot will still work because the browser executes JS
- Keep `max_list_pages` low for first tests (2-3), increase once confirmed working
