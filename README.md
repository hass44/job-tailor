# Job Tailor

Find jobs on LinkedIn, then get a CV and cover letter tailored to each one, written only from facts in your own CV.

It runs entirely on your own computer, with your own AI key. Nothing is shared with whoever gave you this repo.

## What it does

- **Find jobs:** searches LinkedIn's public job pages with your own job titles, locations and filters. No LinkedIn account needed.
- **Tailor:** gives each job a fit score, the matches and gaps, a CV and a cover letter.
- **PDFs:** builds real PDFs with LaTeX if you have it, or web pages you can save as PDF if you don't.
- **Track:** keeps a status for every application.

You can also skip LinkedIn and paste any job in by hand.

## Setup

You need [Python 3.10 or newer](https://www.python.org/downloads/) (on Windows, tick "Add Python to PATH" in the installer) and [Git](https://git-scm.com/downloads).

```bash
git clone https://github.com/hass44/job-tailor.git
cd job-tailor
```

Then start it:

- **Windows:** double-click `start.bat`
- **Mac or Linux:** run `sh start.sh`

The app opens at http://127.0.0.1:8766. There's nothing to install with pip.

To get updates later, run `git pull`. Your data is not touched.

### Add your AI key

On the **Settings** tab, pick a provider and paste your own key:

| Provider | Where to get a key |
|---|---|
| Claude | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

Then pick a model from the list, or leave it on Automatic. Usage is billed to your key by that provider. If you already use [Claude Code](https://claude.com/claude-code) and are logged in, you can pick it instead and skip the key.

You can also set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY` in your environment instead of saving a key in the app.

### Optional: LaTeX for real PDFs

Install [MiKTeX](https://miktex.org/download) (Windows) or [TeX Live](https://tug.org/texlive/) (Mac: MacTeX; Linux: your package manager), then restart the app. The Settings tab shows whether it was found. MiKTeX downloads a few packages the first time, so allow that if it asks, and keep it set to install missing packages automatically.

To change the look, edit `templates/cv.tex` and `templates/letter.tex`. Without LaTeX, everything still works: documents open as web pages with a **Save as PDF** button.

## Use it

1. **Profile:** add your name, contact details and full CV. Add as much detail as you like, because the CV is the only source of facts. If you want letters in another language, say so under Writing preferences.
2. **Find jobs:** enter job titles and locations, set your filters, and press **Save and search**. It runs slowly on purpose. You can stop at any time and keep what it found.
3. **Jobs:** the list starts with a rough "quick guess" score from shared words. Press **Generate** on the jobs you like. It takes about a minute.
4. Read the **CV** and **Cover letter**, then send them yourself. The app never applies for you.

To search on a schedule, run `python app.py scrape` from your system's task scheduler. Don't run it while the app is open.

## What it checks

Every result is checked before you see it, and sent back to the AI to fix if it fails:

- Any number that is in neither your CV nor the posting
- Letter length, and stock phrases like "passionate" and "leverage"
- Your availability and work-permit sentences, if you set them

After three tries, anything still wrong shows as a **Check before sending** warning, along with PDFs that run over 2 pages for the CV or 1 for the letter. The number check won't catch everything (a reworded skill or job title, for example), so always read the result before you send it.

## Privacy

Your profile, jobs, documents and API keys are stored in the `data/` folder on your computer. That folder is excluded from git, so it can't be pushed by accident as long as you leave `.gitignore` alone. Keys are stored as plain text there, so keep that folder private.

When you press Generate, your CV and the posting go to the AI provider you chose. Searches go to LinkedIn. Nothing else leaves your computer, and the app only accepts connections from your own computer.

## LinkedIn

The search reads LinkedIn's public guest pages, paced at about one request every 2.5 seconds. It backs off when LinkedIn asks it to, and it doesn't log in. Automated access may still go against LinkedIn's terms, so keep searches small and use it at your own risk. The page format can also change without warning; if a search suddenly finds nothing, that's the likely reason.

## Development

Standard library only.

| File | What it does |
|---|---|
| `app.py` | Web server, storage and generation |
| `llm.py` | Claude, OpenAI, Gemini and Claude Code |
| `scraper.py` | LinkedIn search and filters |
| `documents.py` | Web-page and LaTeX output |
| `templates/` | LaTeX layouts |
| `page.html` | The interface |

Run the tests with:

```bash
python -m unittest
```
