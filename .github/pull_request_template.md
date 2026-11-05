## What does this change, and why?

<!-- One thing per PR. Link the issue: "Closes #123" -->

## How did you verify it?

- [ ] `python3 -m pyflakes phosphor.py` prints nothing
- [ ] `python3 tests/test_logic.py` passes
- [ ] `xvfb-run -a python3 tests/test_gui.py` passes
- [ ] I ran the app and tried the change by hand
- [ ] UI change: screenshots attached for **both** dark and light theme
- [ ] Old `data.json` files still load (if I touched the data format)
- [ ] README updated (if user-visible)

## Project principles

- [ ] Still one file, standard library only
- [ ] No telemetry or automatic network calls
- [ ] No background polling that wakes the CPU while idle

## AI assistance

- [ ] I used an AI tool for part of this change (tool/model: ______ )
- [ ] I read and understand every line, and I tested it myself
- [ ] I verified any facts or API behaviour the tool told me
- [ ] This PR was opened by an AI agent; the human operator who reviewed it is: ______
- [ ] I did not use an AI tool

<!-- Disclosing AI use never counts against you. See "AI-assisted development policy" in the README. -->
