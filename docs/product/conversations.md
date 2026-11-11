# Conversations

An account has many threads. A new thread is named "New thread". The person tells the agent to rename a thread or to delete it, and the agent does so.

A message block is one Enter from the person, or one reply from the assistant. Several Enters before a reply are several person blocks.

The model is sent the thread summary, when one exists, plus the latest 10 person blocks and the latest 10 assistant blocks.

The first summary is written when the thread reaches 80 message blocks. It covers the blocks older than that window. A later summary replaces the earlier one and is written from the earlier summary plus the blocks being folded in. Until the first summary exists, blocks outside the window are stored and are not sent.

The person cannot see or edit the summary.

Deleting a thread removes its message blocks, its summary, its name, and its schedules. Knowledge files and other files stay.

A reply is streamed as tokens arrive. The person can stop a turn while it is streaming. The provider call stops. The partial reply is kept. Ending a session does not stop that call.
