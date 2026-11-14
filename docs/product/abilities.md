# Abilities

An ability is a way of working the agent remembers for one account. The agent writes it from practice with the person and opens it when the task needs it.

The agent updates an ability when the person says to change it. The agent removes an ability when the person says to remove it.

An ability has a name and a text. The agent creates it, and changes or removes it by its id. Two abilities may share a name. The agent reads the list of abilities with their text, so it needs no limit on opened abilities.

An account may have any number of abilities. The profile panel lists them for reading. Detail is in `docs/product/screens.md`.

A schedule the person told the agent to run may use a remembered ability. An ability runs while the person is away only as part of that schedule.

A remembered ability that would submit still requires the confirmation in `docs/product/confirmations.md`.

This build runs the built-in tools. It does not run model-written code.

Deleting the profile does not remove abilities. Deleting the account does.
