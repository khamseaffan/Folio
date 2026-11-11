# Browser

Chrome runs on the Cloud Run worker. The browser kit is unnamed until the first browser task is built. The worker drives the browser.

Cookies are kept between tasks for the same account. They are envelope-encrypted with Cloud KMS and deleted with the account.

The person can sign the browser into a site. A send on that site still requires the confirmation in `docs/product/confirmations.md`.

The person can watch a browser task they started. A schedule runs without them watching. When they are not watching, they see which pages were opened and the final message or report.

There is no list of sites the browser may not open, and no list of sites that skip a confirmation. A page read needs no confirmation.

A browser task has no time limit of its own. Page text and screenshots go to the model, and those calls count toward the token cap in `docs/product/models.md`. The task stops when the person tells the agent to stop it, or when the account reaches that cap.

An account may run more than one browser task at a time. Two tasks may open different sites together. Two tasks may not drive the same signed-in site at the same time. The second task waits.
