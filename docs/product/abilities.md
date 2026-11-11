# Abilities

An ability is a way of working the agent remembers for one account. The agent writes it from practice with the person and opens it when the task needs it.

The agent updates an ability when the person says to change it. The agent removes an ability when the person says to remove it.

An account may have any number of abilities. The profile panel lists them for reading. Detail is in `docs/product/screens.md`.

A schedule the person told the agent to run may use a remembered ability. An ability runs while the person is away only as part of that schedule.

A remembered ability that would submit still requires the confirmation in `docs/product/confirmations.md`.

This build runs the built-in tools. It does not run model-written code.

Deleting the profile does not remove abilities. Deleting the account does.
