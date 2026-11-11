# Conversations

An account has many threads. A new thread is named "New thread". The person tells the agent to rename a thread or to delete it, and the agent does so.

A message block is one Enter from the person, or one reply from the assistant. Several Enters before a reply are several person blocks. A message has no length limit.

One call is sent the system prompt, `profile.md`, the thread summary when one exists, and as many of the newest blocks as fit in that call. The latest 10 person blocks and the latest 10 assistant blocks are included.

Blocks that do not fit stay stored. They are folded into the summary before the call. A later summary replaces the earlier one and is written from the earlier summary plus the blocks being folded in. The person is not told a message is too long.

The person cannot see or edit the summary.

Deleting a thread removes its message blocks, its summary, its name, and its schedules. Knowledge files and other files stay.

A reply is streamed as tokens arrive. The person can stop a turn while it is streaming. The provider call stops. The partial reply is kept. Ending a session does not stop that call.

The thread name shows a status mark while a run is working or waiting. A waiting question appears in that thread. Inside the thread the person sees the assistant's messages.
