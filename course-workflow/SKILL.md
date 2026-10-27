---
name: entrepreneurship-course-project-bootstrap
description: Initialize and guide a student workspace for the Xiamen University Entrepreneurship Management Theory and Frontiers course, including weekly research, AI discussion, and Chinese academic document deliverables.
---

# Entrepreneurship Course Project Bootstrap

Use this skill when a student asks to set up a course project, prepare a weekly discussion assignment, or apply the included course workflow. The package is portable: do not install it into an agent's personal skill directory unless the user explicitly asks.

## Project initialization

1. Inspect the selected workspace and existing files before proposing changes. Preserve existing work; do not initialize Git automatically.
2. Read [course profile and setup](references/course-defaults-and-setup.md). Treat the course and instructor details as package defaults, but leave each student's name, ID, and cohort blank until supplied.
3. Identify only missing preferences that materially change the project. Present a concrete setup plan, then create the README, project constraints, workflow, formatting rules, templates, `.tmp`, and only the active `第N周` directory. Do not pre-create every week.
4. Check the required local tools. Follow [tool availability and installation choices](references/tools-and-fallbacks.md). Never install software without the student's explicit choice.
5. When creating or editing DOCX/PDF files, follow [formatting and deliverables](references/formatting-and-deliverables.md). Keep student data blank in reusable templates.
6. After setup, report created paths and explain how to use the workspace. Explicitly tell the student to start one **new conversation for each week's assignment**; all topics for that week can remain in that new conversation, with continuity carried by project files.

## Weekly assignments

For a weekly assignment, read [weekly workflow and conversation isolation](references/weekly-workflow.md). For literature search, Zotero, and discussion behavior, read [research and discussion rules](references/research-and-discussion.md). Read [AI use evaluation](references/ai-use-evaluation.md) only when the assignment includes AI-process reflection.

Use the supplied `启动提示词.md` when the agent cannot load skills or references. Do not assume the student has Zotero Buddy, LibreOffice, or Office CLI; check availability and explain alternatives.

## Approval boundaries

- Keep teacher-provided assignments distinct from examples and suggestions. Do not invent assignment topics, literature, metadata, or conversation history.
- Ask the student to confirm the end of each discussion topic before moving to the next topic. Do not generate final DOCX/PDF until all topics are confirmed complete and the student explicitly authorizes the final deliverable.
- Do not send or upload this package or project files to other people or services. Do not install or uninstall this skill automatically.
- If an external tool or write action requires authorization, explain the exact action and wait for the student's choice.
