# Submit confirmation

`browser_submit` is the only tool that sends data. A public page read, a file read, a file write inside the account, and a Drive-copy read do not require a confirmation.

A submit runs only after the person confirms the card for that submit. The card shows the site, the field names, the field values, and whether money or personal data is in the fields.

The confirmation belongs to that account and to that card. A different site, field, value, or amount needs a new card. The confirmation is consumed when that submit runs. An unused confirmation does not authorize a later submit.

The person's words are stored with the confirmation as the reason they agreed. Those words do not authorize a submit.

The model may propose the submit. Code sends only when an unused confirmation for this account matches the site and the fields on the card. A missing confirmation, a used confirmation, a confirmation from another account, or a mismatch does not send.

A task that would cause physical harm stops and asks. There is no tool that moves money or touches the physical world except through `browser_submit`.
