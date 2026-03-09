# MVP Development Flow (Linear + Agents)

This document defines a clean, automated flow for turning Linear ideas into reviewed MVPs.

## Goal

Move every ticket through a consistent pipeline:

`Ideas -> Planning -> Build -> Test -> Review`

Each state is owned by a dedicated agent with clear input/output contracts.

---

## Workflow Summary

1. **Ideas**  
   Brainstorm agent refines the raw idea into a clear concept and acceptance criteria.
2. **Planning**  
   Planning agent researches implementation options and creates an execution plan.
3. **Build**  
   Build agent implements MVP according to the plan.
4. **Test**  
   Test agent validates behavior, fixes defects, and verifies quality gates.
5. **Review**  
   Human review for final product judgment and direction.

---

## State Contracts

### 1) Ideas (owned by Brainstorm Agent)

**Input**
- Original ticket title + description
- Any linked context/docs

**Agent Responsibilities**
- Clarify the user problem, target user, and expected value
- Remove ambiguity and define scope boundaries
- Propose a minimal MVP slice
- Add explicit non-goals
- Add acceptance criteria and success metric

**Required Output (written back to ticket)**
- Problem statement (1-2 paragraphs)
- MVP scope (in / out)
- Acceptance criteria checklist
- Risks + assumptions
- Suggested implementation direction (high-level)

**Exit Criteria**
- Ticket has enough clarity to plan implementation
- Acceptance criteria are testable

**Transition**
- Move issue from **Ideas -> Planning**

---

### 2) Planning (owned by Planning Agent)

**Input**
- Refined concept from Ideas stage

**Agent Responsibilities**
- Do technical research relevant to the stack
- Break work into concrete implementation steps
- Identify dependencies, migration needs, and rollout strategy
- Define test plan (unit/integration/e2e as needed)
- Define observability and rollback approach for risky changes

**Required Output**
- Technical design summary
- Task breakdown with sequence
- Risks + mitigations
- Test strategy
- "Definition of Done" checklist

**Exit Criteria**
- Build agent can execute without guessing architecture
- Scope is still MVP-sized

**Transition**
- Move issue from **Planning -> Build**

---

### 3) Build (owned by Build Agent)

**Input**
- Planning artifacts and DoD checklist

**Agent Responsibilities**
- Implement the smallest complete version that satisfies acceptance criteria
- Keep commits scoped and traceable
- Update docs/configs as needed
- Record implementation notes and tradeoffs on the ticket

**Required Output**
- Working implementation
- Short build summary (what changed, why)
- Known limitations
- Test instructions (how to verify)

**Exit Criteria**
- Feature implemented and runnable
- Relevant tests added/updated

**Transition**
- Move issue from **Build -> Test**

---

### 4) Test (owned by Test Agent)

**Input**
- Build output + acceptance criteria + test instructions

**Agent Responsibilities**
- Execute test plan (automated + manual spot checks)
- Confirm acceptance criteria one-by-one
- Fix defects found during testing (or create linked bug issues)
- Re-run tests after fixes

**Required Output**
- Test report:
  - What was tested
  - Pass/fail results
  - Defects found/fixed
  - Residual known issues

**Exit Criteria**
- All required acceptance criteria pass
- No unresolved blocker defects

**Transition**
- Move issue from **Test -> Review**

---

### 5) Review (owned by Humans)

**Input**
- Final implementation + test report

**Human Responsibilities**
- Validate product quality and UX
- Check alignment with business goal
- Approve, request refinements, or re-scope next iteration

**Possible Outcomes**
- Approve and close
- Return to **Build** for product changes
- Return to **Planning** if architecture/scope change is required

---

## Automation Rules

For each state, automation should enforce:

1. **Single owner** per stage (agent role)
2. **Required template output** before transition
3. **Checklist gate** validating exit criteria
4. **Automatic state move** when checklist is complete
5. **Fallback routing** on failure/timeouts

---

## Suggested Ticket Templates

### Brainstorm Output Template

```md
## Problem
...

## MVP Scope
- In:
- Out:

## Acceptance Criteria
- [ ]
- [ ]

## Risks / Assumptions
- ...
```

### Planning Output Template

```md
## Technical Design
...

## Implementation Plan
1.
2.

## Test Strategy
- Unit:
- Integration:
- E2E/manual:

## Definition of Done
- [ ]
- [ ]
```

### Test Report Template

```md
## Coverage
...

## Results
- AC1: pass/fail
- AC2: pass/fail

## Defects
- fixed:
- remaining:
```

---

## Operational SLAs (recommended)

- Ideas triage: < 24h
- Planning completion: < 48h
- Build first pass: < 3 working days
- Test turnaround: < 24h after build handoff

If an agent exceeds SLA, auto-comment with status and escalate to human owner.

---

## Minimal State Machine

```text
Ideas
  -> Planning   (brainstorm output complete)
Planning
  -> Build      (plan + DoD complete)
Build
  -> Test       (implementation + tests ready)
Test
  -> Review     (all required checks pass)
Review
  -> Closed     (approved)
  -> Build      (change requested)
  -> Planning   (scope/architecture shift)
```

---

## Definition of "Clean"

This process is considered clean when:
- Every transition is criteria-based (not ad hoc)
- Every stage leaves auditable artifacts in the ticket
- There is a predictable path backward when quality fails
- Human review is focused on product decisions, not missing context

