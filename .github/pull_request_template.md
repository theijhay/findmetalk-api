## Requirement IDs

<!-- Every ID this PR implements or touches, e.g. FR-AUTH-1, FR-AUTH-4, TR-4. -->

## Change in behaviour

<!-- What a user, a client or an operator sees differently. Not a file list. -->

## Migration

<!-- "None", or the revision id and what it does. A PR with a migration says so in its first line too. -->

## Contract

<!-- "Unchanged", or which routes/events changed in contract/ and whether the change is breaking for findmetalk-web / findmetalk-admin. -->

## Safety, recording and privacy (spec 19)

<!-- Effect on block/report/moderation, recording (REC-*), PII and logs. "None" only if truly none. -->

## OPEN items

<!-- Any OPEN item this depended on, and the owner's answer (link or quote). "None" if none. -->

## What the tests prove, and what they do not

<!-- Name the tests. Say plainly what is not covered (e.g. "no load test", "iOS not tested"). -->

## Checklist

- [ ] `make check` passed locally (lint, types, contract, unit + integration tests, migrations)
- [ ] New settings added to `.env.example` with a comment on what they gate
- [ ] No PII in logs; no secrets in code
- [ ] User-facing copy touching crisis, health, recording consent or legal is marked `NEEDS REVIEW`
- [ ] Nothing from `findmetalk-docs/` is committed
