# Make Treatment Protocol v1 canonical in a pure domain module

Zema will define Treatment Protocol v1 in a pure domain module and treat the database protocol rows as a validated persistent mirror. Due State and Adherence will share phase and Treatment Slot primitives but remain separate strategies because they answer different questions; time and deployment timezone are explicit inputs so callers and tests do not depend on hidden global clocks.

## Considered options

- Database-only authority was rejected because several callers had already reconstructed protocol and slot rules independently.
- Code-only authority without database validation was rejected because persisted protocol rows could silently disagree with runtime calculations.
- One unified Due State and Adherence calculation was rejected because operational next action and historical credit have intentionally different semantics.

## Consequences

A mismatch between canonical v1 and its database mirror prevents Zema from becoming healthy after migration and bootstrap. Zema reports the differing phase and field, does not start the scheduler, and never overwrites protocol or treatment data automatically.
