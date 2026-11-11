# Architecture

About 25 people, with room for a few hundred. One region: `us-central1`.

```mermaid
flowchart LR
  person[Person]
  firebase["Firebase Authentication"]
  drive["Google Drive"]
  webapp["Next.js on Vercel"]
  api["Cloud Run API"]
  sql["Cloud SQL"]
  storage["Cloud Storage"]
  kms["Cloud KMS"]
  tasks["Cloud Tasks"]
  scheduler["Cloud Scheduler"]
  worker["Cloud Run worker"]
  model["Model provider"]
  publicweb["Public web"]

  person -->|HTTPS| webapp
  person -->|sign in| firebase
  webapp -->|sign in| firebase
  webapp -->|calls| api
  drive -->|"read-only import"| api
  api -->|"read and write"| sql
  api -->|"file bytes"| storage
  api -->|"wrap key"| kms
  api -->|enqueue| tasks
  scheduler -->|"when due"| tasks
  tasks -->|retry| worker
  worker -->|model| model
  worker -->|"when the task needs it"| publicweb
  worker -->|"save result"| sql
```

- Chat returns on the API request.
- Research and scheduled runs go through the queue.
- Research and a scheduled run may read the public web when the task needs it.
- A file prefix belongs to one account. The platform holds the model keys.
- Vercel hosts the web app. Google Cloud holds data, jobs, keys, and the API.
- Firebase Authentication holds the sign-in. Postgres holds the account.
