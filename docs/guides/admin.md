# Relay CRM: guide for Admins

Every change you make asks for a reason and is saved in the history.

## People, teams, assignment
- **Users / Teams**: add people and teams, change roles, move reps (choose whether their open deals move with them or go back to the Pool).
- **Assignment**: default team, rules (first match wins, lowest number first), rep groups, lead caps per rep, and a **Test a lead** tool that shows where a sample lead would go without creating anything.

## The pipeline, without a developer
**Pipeline** has versions. Only one draft exists at a time; published versions are read-only and kept forever.
1. **Start a draft** from the live version. Rename, add, remove or reorder stages; set probabilities; edit transitions; edit each stage's exit criteria and the proof each one needs.
2. **Check draft** lists problems in plain words (one entry stage, every stage reachable, a way forward from each stage, valid proof rules).
3. **Preview impact** shows open deals per stage. A stage you remove that still holds deals needs a target stage; each deal is moved there and every move is logged.
4. **Publish** (reason). Deals already in a stage keep the checklist they entered with; deals entering a stage later get the new one.
5. **Roll back** re-publishes an older version as a new version number.

## Settings lists
**Settings lists**: lost reasons, the fit checklist reps tick in the Lead stage, and custom fields (text, number, date, dropdown, yes/no, file, contact) on deals, contacts and companies. Entries are hidden, never erased.

## Catalog and prices
**Catalog**: products, list price and floor price. A price change never touches lines already on deals.

## Approvals you decide
Discounts above 10% or below the floor, value changes above 10% made by a Team Leader, invoice edits, role changes, team transfers. You cannot approve your own request or one about a deal you own.

## Other powers
Bulk reassign a departing rep's deals; move a deal between teams directly; terminate an agreement; delete and restore deals; reopen lost deals; export (with a reason; logged); open anyone's Today and profile; compare teams.

## Watching the system
**Failed jobs** shows background work that gave up; retry it from there. The audit trail (history on every record) shows who, when, what and why.
