---
name: senior-python-rules
description: Use when writing, changing, reviewing or debugging Python code: senior-style rules on simplicity, no new dependencies, pydantic contracts, root-cause debugging and automating repetition.
---

# Senior Python rules

Apply these rules to every Python task: new code, changes, reviews, and debugging. They describe how a senior engineer on this team works. When a rule conflicts with a habit of yours, the rule wins. When a rule conflicts with an explicit instruction from the user, the user wins.

## Before coding
1. **Business logic first.** Before writing code, state in plain words what the feature must do: its inputs, outputs, and edge cases. If any of that is unclear, ask before coding. Code written against a guessed requirement is waste.
2. **Read the surrounding code.** Match the patterns already in the repo. Do not introduce a second way to do something that the repo already does one way.

## Writing code
3. **No new packages, dependencies, or services.** Solve the problem with the standard library and what is already in the repo. If you believe a new dependency is truly needed, stop and ask, explaining what it replaces and why existing tools cannot do it.
4. **Keep it stupid simple.** Pick the most obvious solution, the one a tired person can read at 3am. No layers, patterns, options, or config that the current task does not need. No speculative features.
5. **Pydantic models are the contract.** All data crossing a boundary (API requests and responses, database rows, files, environment, external services) is a pydantic model. Parse once at the edge, then pass typed models. No raw dicts passed between functions.
6. **Automate repetition.** When the same shape appears a third time (requests, parsing, conversions, mappings), build one typed helper, decorator, or parser that generates it, so each new case is a few declarative lines. This is where the team gets its speed.
7. **Use advanced Python when it earns its place.** Generics, protocols, context managers, pydantic validators, and similar features are welcome when they make the code that uses them shorter and harder to get wrong. Never use them to show off, and keep the clever part small and in one place.

## The quality bar
The reference for what good Python looks like is the Cisco Catalyst WAN SDK: https://github.com/cisco-en-programmability/catalystwan-sdk

Take its ideas, not its mechanisms. Do not copy its decorators or structure into this project. What to take from it:
- Everything is typed, and the types are the documentation.
- Pydantic models describe every payload and every response.
- Layers are clearly separated: data models, low-level access, and a high-level API built on top.
- Repetitive work is declared in a few lines and done by shared code, not written by hand each time.
- Each piece is small, readable, and does one thing.

For how to do something in this project, follow the patterns this project already uses.

## Debugging
8. **Debug like a caveman.** Reproduce the bug first. Add prints or logs and narrow it down until you can see the actual root cause. Do not theorize past the evidence.
9. **Fix the cause with the smallest change.** Never patch symptoms. Do not add retries, fallbacks, default values, or try/except blocks to make an error go away. If the root cause is outside the code you can change, say so.

## Finishing
10. **It must work.** Run the code and show the output before saying it is done. Never claim something works without having seen it work.
11. **Cover critical logic with tests.** That means money, auth, data writes, parsing of external data, and every shared helper from rule 6. Test behaviour through public interfaces. Do not write tests for trivial glue.
12. **Report plainly.** Say what changed, what you ran, and anything you were unsure about or left undone.
