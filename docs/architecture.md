# Architecture

About 25 people, with room for a few hundred. One region: `us-central1`.

```mermaid
flowchart LR
  person[Person]
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
  webapp -->|calls| api
  drive -->|"read-only import"| api
  api -->|"read and write"| sql
  api -->|"file bytes"| storage
  api -->|"wrap key"| kms
  api -->|enqueue| tasks
  scheduler -->|"when due"| tasks
  tasks -->|retry| worker
  worker -->|"their key"| model
  worker -->|"research only"| publicweb
  worker -->|"save result"| sql
```

- Chat returns on the API request.
- Research and scheduled runs go through the queue.
- Research may read the public web. A scheduled run may not.
- A model key and a file prefix belong to one account.
- Vercel hosts the web app. Google Cloud holds data, jobs, keys, and the API.
