# Drive

Drive is the integration. An import is one file the person picks in Google's picker. The app stores a private read-only copy of that file from that moment. A later edit in Drive waits for another import.

One account connects one Google account. The refresh token is envelope-encrypted with Cloud KMS and belongs to that account.

The assistant may ask for a new import during a task. The person starts the import.

The person can disconnect Drive. Disconnecting removes the token and leaves the copies. Deleting the account removes the token and the copies.

A confirmed schedule may read copies already imported. It does not contact Google, start an import, or write to Drive.

Drive copies are outside the byte count in `docs/product/files.md`. Reading a copy does not require a submit confirmation.
