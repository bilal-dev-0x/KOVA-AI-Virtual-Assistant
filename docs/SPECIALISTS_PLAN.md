# KOVA specialist agents: design for review

**Status: proposal only. No specialist-agent code should be built until Bilal approves this design.**

## Goal

Let Max split a genuinely multi-domain request among System, Coding, Research, and Media specialists, while one small planner keeps the work ordered. Specialists suggest typed tool calls; they never execute desktop or file operations themselves.

## Shape

1. The existing `Agent` receives the user request and keeps ownership of the conversation, permissions, and final answer.
2. A planner may return a bounded JSON list of steps. Each step names one specialist and a short task. Reject malformed output, unknown specialists, nested plans, and plans above six steps.
3. Each specialist gets only its task, relevant prior step results, and a narrow allowlist of existing registered tools. It returns structured tool calls or a plain finding; it cannot add tools or change the plan.
4. `ActionRunner` remains the only executor. Every call goes through schema validation, permission checks, approval, logging, and undo handling. A refusal or failed step stops the plan. A user approval covers only the exact actions shown in that approval.
5. Max reports the findings and the actions that actually succeeded. Specialist prose is untrusted data: it cannot override system rules, user intent, or approval state.

## Specialist boundaries

| Specialist | Intended work | Initial tool boundary |
| --- | --- | --- |
| System | Read system status and manage ordinary windows/apps | Read-only status, open/focus/minimise; risky close remains explicitly approved |
| Coding | Inspect a named project, run its tests, explain a failure | Project listing/summary, test runner, git status/diff; no arbitrary shell or file writes |
| Research | Find and compare public information | Existing search/browser tools; no account actions, purchases, or message sending |
| Media | Find/play/pause local or public media | Existing media and playback tools; no upload, purchase, or sharing |

Tool access must be filtered from the registered-tool metadata for every call, not trusted from model instructions alone. Begin with read-only specialists where possible; add controlled actions only after separate tests.

## Limits and failure behavior

- One planner request and at most one request per specialist step; no parallel model calls on Bilal's laptop.
- Reuse the configured brain chain; no local model, new heavyweight dependency, or always-on worker.
- A specialist error, timeout, invalid tool, missing result, or changed foreground window stops the plan and is reported plainly.
- Preserve the original user request for intent checks. Never treat a specialist's words as user authorization.
- Cap tool calls and response sizes; do not pass API keys, full config, unrelated memory, or raw private logs to a specialist.

## Test plan

- Planner accepts a valid bounded plan and rejects invalid JSON, unknown roles, nested delegation, and more than six steps.
- Every role is blocked from tools outside its allowlist, even if the model requests them.
- All calls still pass through `ActionRunner`; test SAFE, CONTROLLED, CRITICAL, refusal, failure, and undo paths.
- A specialist cannot grant approval, change the original user intent, or claim an action succeeded without a successful tool result.
- A failed step halts later steps and the final answer lists only completed actions.
- Verify request caps, timeout handling, and that secrets/private history are excluded from prompts.

## Approval gate

Bilal should approve this design before implementation. The first build should cover planner validation and one read-only System specialist; add the other specialists only after that slice passes tests and a live review.
