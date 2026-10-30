# Job Tailor

Paste a job posting, get a CV and cover letter tailored to it, written only from facts in your own CV.

It runs on your computer. Add your details and CV once, then for each job paste the posting and press Generate. You get a fit score, the matches and gaps, a CV and a letter you can save as PDF, and a simple tracker for where each application stands.

## Setup

You need [Python 3.10 or newer](https://www.python.org/downloads/) (on Windows, tick "Add Python to PATH" in the installer). Nothing else to install.

Download this repo, then:

- **Windows:** double-click `start.bat`
- **Mac or Linux:** run `sh start.sh`

The app opens at http://127.0.0.1:8766.

## Connect Claude

Pick one, on the **Settings** tab:

- **Claude Code (no key needed):** [install it](https://claude.com/claude-code), run `claude` once in a terminal and log in. The app finds it automatically.
- **API key (paid per use):** create one at [console.anthropic.com](https://console.anthropic.com) and paste it in. Reading PDF CVs needs this option; with Claude Code, upload a DOCX or TXT, or paste the text.

## Use it

1. **Profile:** add your name, contact details and full CV. Add as much detail as you like, because the CV is the only source of facts.
2. **Jobs:** paste the title, company and posting text, then press **Add and generate**. It takes about a minute.
3. Open **CV** and **Cover letter**, read them, then press **Save as PDF**. In the print window, turn off headers and footers.

## What it checks

Every result is checked before you see it, and sent back to Claude to fix if it fails:

- Any number that is in neither your CV nor the posting
- Letter length, and stock phrases like "passionate" and "leverage"
- Your availability and work-permit sentences, if you set them

If a problem is still there after three tries, the job shows a **Check before sending** warning. The number check won't catch everything (a reworded skill or job title, for example), so always read the result before you send it.

## Privacy

Your profile, jobs, documents and API key are stored in `data/store.json` on your computer. That folder is excluded from git, so don't remove it from `.gitignore`. When you press Generate, your CV and the posting are sent to Claude, either through the API or through Claude Code. The app only accepts connections from your own computer.

## Development

Standard library only: `app.py` is the server and `page.html` is the interface. Run the tests with:

```bash
python -m unittest
```
