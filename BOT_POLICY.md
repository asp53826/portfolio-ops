# Automation policy

## Authorship

Automated commits use the GitHub Actions bot identity. The system does not set
the repository owner's email as an author or co-author and does not attempt to
alter the owner's contribution graph.

## Allowed autonomous changes

- deterministic generated documentation and telemetry;
- patch-level Dependabot updates after all repository checks pass;
- issue creation, update and closure inside repositories owned by `asp53826`;
- releases that are explicitly initiated by pushing a signed or intentional
  `v*` tag;
- retention of benchmark, build and health evidence as workflow artifacts.

## Human-review boundary

Minor and major dependency upgrades, source-code fixes, benchmark-claim
changes, permission expansion, new secrets, spending, legal acceptance and all
interactions in repositories owned by other people require human review.

## Prohibited behavior

- empty or meaningless commits, issues, pull requests and reviews;
- automated stars, follows, reactions or accepted-answer activity;
- fake accounts, fabricated collaboration or impersonated authorship;
- retroactive timestamp manipulation or activity-calendar painting;
- unreviewed posting to third-party repositories;
- invented benchmarks, download counts, certifications or achievements.

## Failure behavior

Monitors retry transient failures and maintain a single issue per failing
surface. Recovery closes the issue. Repeated failures do not create additional
issues or comments.
